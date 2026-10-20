"""W2-T019: автоматическое решение «запускать ли полный P2-2a» по базовому прогону на арендованной машине.

Базовый прогон — тот же стенд admission_p2_2.py в малом объёме (по умолчанию 4 ед./ячейку,
D/T и B на 1 год) на всех рабочих процессах сразу. Из его analysis.json берётся факт/модель
(wall / модель плана §5) по вариантам — это скорость ИМЕННО этой машины под полной нагрузкой,
в единицах модели плана (US_PER_STEP калиброван на P-ядрах i9-14900KF).

Прогноз полного P2-2a (W2-P006: 128 и 16 ед./ячейку, D/T на 1/10/100/1000 лет, B на 100/1000):
  объём       = Σ_v модель_v(все задания, выживание по H3 при c=0.5, с запасом к W2-R017) × r_v / W;
  путь D/T    = самое долгое задание D или T (выживает до 1000 лет) × r_v;
  путь B      = самое долгое задание B на 1000 лет × r_B.
По W2-D026 остановленные сроком B решение не блокируют, поэтому:
  GO       — max(объём, путь D/T) × запас ≤ остаток до срока стенда;
  FAILED   — иначе, или данных базового прогона мало, или в нём есть отказы/остановки;
  внимание — если путь B × запас > остатка: часть B на 1000 лет будет остановлена сроком
             (описательная часть беднее, решение D–T не страдает).

    python scripts/baseline_gate_p2_2.py --baseline runs/W2-P2-2a-baseline-<id> --workers 32 \
        --deadline-utc 2026-10-16T10:30:00Z --output runs/W2-P2-2a-<id>/baseline_gate.json

Код выхода: 0 — GO, 1 — FAILED (полный стенд не запускать).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))

YEAR = 365.25*86400.0
# Выживание по H3 a(1+e) <= c·RH0: c=0.5 — 48% выживших; факт W2-R017 — 28.5% уже на 10 лет,
# т. е. объём завышен примерно в 1.7 раза (запас). c=0.7 (63%) — верхняя вилка плана §5.
SURVIVAL_C = 0.5
MIN_JOBS = dict(D=20, T=20, B=10)
MIN_MODEL_SECONDS = dict(D=5.0, T=5.0, B=30.0)


def predict(cost, volumes, longest, workers, available_hours, margin):
    """Чистый расчёт решения. cost — analysis['cost'] базового прогона; volumes/longest — модель
    плана в секундах по вариантам; available_hours — от сейчас до срока стенда."""
    problems = []
    ratios = {}
    for v in ('D', 'T', 'B'):
        c = cost.get(v)
        if not c or c.get('jobs', 0) < MIN_JOBS[v] or (c.get('model_core_hours') or 0)*3600 < MIN_MODEL_SECONDS[v]:
            problems.append(f'мало данных базового прогона для {v}: '
                            f"заданий {0 if not c else c.get('jobs')}, модель {0 if not c else c.get('model_core_hours', 0)*3600:.1f} с")
            continue
        ratio = c.get('measured_over_model')
        if ratio is None or not math.isfinite(ratio) or ratio <= 0:
            problems.append(f'нет отношения факт/модель для {v}')
            continue
        ratios[v] = float(ratio)
    if workers < 1:
        problems.append('число процессов < 1')
    out = dict(ratios=ratios, workers=workers, margin=margin, available_hours=available_hours,
               model_volume_core_hours={v: s/3600 for v, s in volumes.items()},
               model_longest_hours={v: s/3600 for v, s in longest.items()})
    if problems:
        return dict(out, verdict='FAILED', reasons=problems, warnings=[])
    volume = sum(volumes[v]*ratios[v] for v in volumes)/workers/3600
    path_dt = max(longest['D']*ratios['D'], longest['T']*ratios['T'])/3600
    path_b = longest['B']*ratios['B']/3600
    need_dt = max(volume, path_dt)*margin
    reasons, warnings = [], []
    if need_dt > available_hours:
        reasons.append(f'D/T не укладываются: нужно {need_dt:.2f} ч с запасом ×{margin}, до срока {available_hours:.2f} ч')
    if path_b*margin > available_hours:
        warnings.append(f'самое долгое B {path_b:.2f} ч (×{margin} = {path_b*margin:.2f}) > {available_hours:.2f} ч: '
                        'часть B на 1000 лет будет остановлена сроком (W2-D026: решение D–T не блокирует)')
    return dict(out, verdict='FAILED' if reasons else 'GO', reasons=reasons, warnings=warnings,
                projected_hours=dict(volume=volume, path_dt=path_dt, path_b=path_b, need_dt=need_dt),
                i9_equivalent_speed={v: 1/ratios[v] for v in ratios})


def plan_model(stand, per_cell=128, b_per_cell=16, years=(1.0, 10.0, 100.0, 1000.0), b_years=(100.0, 1000.0),
               survival_c=SURVIVAL_C):
    """Модель плана §5 для полного P2-2a: объём (с выживанием по H3 при survival_c) и самые долгие задания."""
    from plan_p2_2 import cost_seconds
    unit_list = stand.units(per_cell)
    volumes, longest = dict(D=0.0, T=0.0, B=0.0), dict(D=0.0, T=0.0, B=0.0)
    for u in unit_list:
        point = dict(host=u['host'], a_over_rh=u['a_over_rh'], e=u['eccentricity'], period_s=u['period_seconds'])
        for v in ('D', 'T', 'B'):
            horizons = b_years if v == 'B' else years
            if v == 'B' and u['unit'] >= b_per_cell:
                continue
            for h in horizons:
                volumes[v] += cost_seconds(point, v, h, survival_c)[0]
            longest[v] = max(longest[v], stand.model_seconds(u, max(horizons), v))
    return volumes, longest


def stand_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location('admission_p2_2', ROOT/'scripts'/'admission_p2_2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    parser.add_argument('--baseline', required=True, help='каталог базового прогона (analysis.json, summary.json)')
    parser.add_argument('--workers', type=int, required=True)
    parser.add_argument('--deadline-utc', required=True, help='срок полного стенда, ISO 8601 UTC')
    parser.add_argument('--margin', type=float, default=1.15)
    parser.add_argument('--survival-c', type=float, default=SURVIVAL_C)
    parser.add_argument('--output', required=True)
    parser.add_argument('--now-utc', default=None, help='для проверки; по умолчанию текущее время')
    args = parser.parse_args(argv)
    base = ROOT/args.baseline
    analysis = json.loads((base/'analysis.json').read_text(encoding='utf-8'))
    summary = json.loads((base/'summary.json').read_text(encoding='utf-8'))
    deadline = datetime.fromisoformat(args.deadline_utc.replace('Z', '+00:00'))
    now = datetime.fromisoformat(args.now_utc.replace('Z', '+00:00')) if args.now_utc else datetime.now(timezone.utc)
    available = (deadline-now).total_seconds()/3600
    stand = stand_module()
    volumes, longest = plan_model(stand, survival_c=args.survival_c)
    result = predict(analysis.get('cost') or {}, volumes, longest, args.workers, available, args.margin)
    if summary.get('failed') or summary.get('stopped'):
        result['verdict'] = 'FAILED'
        result['reasons'] = result.get('reasons', [])+[
            f"в базовом прогоне отказов {summary.get('failed')}, остановлено {summary.get('stopped')}"]
    result.update(baseline=args.baseline, deadline_utc=args.deadline_utc, now_utc=now.isoformat(),
                  machine=summary.get('machine'), survival_c=args.survival_c, task='W2-T019')
    path = ROOT/args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    p = result.get('projected_hours') or {}
    print(f"BASELINE {result['verdict']}: факт/модель {result['ratios']}; процессов {args.workers}; "
          f"объём {p.get('volume', float('nan')):.2f} ч, путь D/T {p.get('path_dt', float('nan')):.2f} ч, "
          f"путь B {p.get('path_b', float('nan')):.2f} ч; до срока {available:.2f} ч")
    for line in result['reasons']:
        print('  причина:', line)
    for line in result.get('warnings', []):
        print('  внимание:', line)
    return 0 if result['verdict'] == 'GO' else 1


if __name__ == '__main__':
    raise SystemExit(main())
