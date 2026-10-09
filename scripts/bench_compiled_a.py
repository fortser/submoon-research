"""W2-T008, этап F: прототип скомпилированного DOP853 против scipy-пути движка A.

12 performance_cases, одинаковый горизонт, повторы с чередованием порядка, один
процесс. Сравнение: исходы и события (тип, тело, время), конечное состояние,
число шагов и вычислений силы, доля шагов, ушедших в Python-проверку событий.
Время первой компиляции Numba печатается отдельно. Побитного совпадения нет по
построению. Машинный замер прототипа, не численный допуск и не научный результат.

    ~/venvs/submoon-w2/bin/python scripts/bench_compiled_a.py --periods 20 --repeats 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402

from bench_engines_ac import cpu_info  # noqa: E402
from submoon_research.dynamics.compiled_dop853 import PROTOTYPE_VERSION, integrate_compiled  # noqa: E402
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases, performance_cases, warmup  # noqa: E402

VARIANTS = ('scipy_A', 'compiled')


def run(variant, case, horizon, mode):
    kwargs = dict(period=case['period_seconds'], mode=mode, hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=np.linspace(0, horizon, 65))
    started = time.perf_counter()
    if variant == 'scipy_A':
        out = integrate_engine('dop853_jit', case['state'], case['gms'], case['figures'],
                               case['radii'], horizon, **kwargs)
    else:
        out = integrate_compiled(case['state'], case['gms'], case['figures'], case['radii'],
                                 horizon, **kwargs)
    return time.perf_counter()-started, out


def compare(ref, new, case):
    period, scale = case['period_seconds'], case['a_km']
    fr = np.asarray(ref['final_state']).reshape(-1, 6)
    fn = np.asarray(new['final_state']).reshape(-1, 6)
    row = dict(outcome_ref=ref['physical_outcome'], outcome_new=new['physical_outcome'],
               same_outcome=ref['physical_outcome'] == new['physical_outcome'],
               run_status=(ref['run_status'], new['run_status']),
               end_time_difference_periods=(new['last_valid_time']-ref['last_valid_time'])/period,
               probe_position_difference_over_a=float(np.linalg.norm(fn[-1, :3]-fr[-1, :3])/scale),
               steps=(ref['steps'], new['steps']), nfev=(ref['nfev'], new['nfev']),
               python_event_steps=new['python_event_steps'])
    if ref['event'] or new['event']:
        row['event_ref'] = ref['event']
        row['event_new'] = new['event']
        row['same_event'] = bool(ref['event'] and new['event']
                                 and ref['event']['event'] == new['event']['event']
                                 and ref['event'].get('body_index') == new['event'].get('body_index'))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--periods', type=float, default=20.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    mode = NumericMode()
    _, cases = load_cases(ROOT)
    cases = performance_cases(cases)[:args.limit]
    info = cpu_info()
    print(f"прототип v{PROTOTYPE_VERSION}; CPU: {info.get('lscpu', {}).get('Model name', '?')}; случаев {len(cases)}, "
          f"{args.periods} периодов, повторов {args.repeats}", flush=True)
    warmup()
    started = time.perf_counter()
    run('compiled', cases[0], cases[0]['period_seconds'], mode)
    compile_seconds = time.perf_counter()-started
    run('scipy_A', cases[0], cases[0]['period_seconds'], mode)
    print(f'первый вызов прототипа (компиляция или загрузка кеша Numba): {compile_seconds:.1f} с', flush=True)

    horizons = {c['orbit_id']: c['period_seconds']*args.periods for c in cases}
    walls = defaultdict(lambda: defaultdict(list))
    rows = {}
    for repeat in range(args.repeats):
        order = VARIANTS if repeat % 2 == 0 else VARIANTS[::-1]
        for case in cases:
            outs = {}
            for variant in order:
                wall, outs[variant] = run(variant, case, horizons[case['orbit_id']], mode)
                walls[case['orbit_id']][variant].append(wall)
            rows[case['orbit_id']] = compare(outs['scipy_A'], outs['compiled'], case)
        print(f'повтор {repeat+1}/{args.repeats} готов', flush=True)

    print('\norbit_id | шагов scipy/компил. | в Python | scipy, с | компил., с | ускорение | исход | Δt конца, периодов | Δr/a')
    totals = defaultdict(float)
    for case in cases:
        oid = case['orbit_id']
        best = {v: min(walls[oid][v]) for v in VARIANTS}
        for v in VARIANTS:
            totals[v] += best[v]
        r = rows[oid]
        print(f"{oid} | {r['steps'][0]}/{r['steps'][1]} | {r['python_event_steps']} | {best['scipy_A']:.3f} | "
              f"{best['compiled']:.4f} | x{best['scipy_A']/best['compiled']:.1f} | "
              f"{r['outcome_ref']}{'' if r['same_outcome'] else ' != '+r['outcome_new']} | "
              f"{r['end_time_difference_periods']:.1e} | {r['probe_position_difference_over_a']:.1e}")
    steps = sum(r['steps'][0] for r in rows.values())
    print(f"ИТОГО: scipy A {totals['scipy_A']:.2f} с, прототип {totals['compiled']:.3f} с, "
          f"x{totals['scipy_A']/totals['compiled']:.1f}; мкс/шаг: {1e6*totals['scipy_A']/steps:.0f} -> "
          f"{1e6*totals['compiled']/steps:.1f}")
    same = all(r['same_outcome'] and r.get('same_event', True) for r in rows.values())
    print('исходы и типы событий совпали во всех случаях:', 'ДА' if same else 'НЕТ')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-T008_compiled_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(task='W2-T008', stage='F', kind='prototype_benchmark_not_numeric_admission',
        utc=stamp, prototype_version=PROTOTYPE_VERSION, machine=info, periods=args.periods, repeats=args.repeats, numeric=mode.__dict__,
        first_call_seconds=compile_seconds, walls={k: dict(v) for k, v in walls.items()},
        totals_min_seconds=dict(totals), rows=rows, all_outcomes_and_events_agree=same),
        ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0 if same else 1


if __name__ == '__main__':
    raise SystemExit(main())
