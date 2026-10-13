"""W2-T008: разбор P2-R1/P2-R2 по готовому run P2-1 (время расхождения и высокие e).

Не научный допуск и не новый расчёт: только чтение jobs.jsonl выбранного run.
Расхождение пары X–Y в момент сетки t: d = max(|Δr|/a, |Δv|/(n a)) — то же определение, что в
admission_p2_horizons.compare (порог 1e-6 задаёт τ). Для каждой орбиты и пары строится кривая
d(t) на общей сетке; на участке 1e-13 < d < 1e-4 (выше шума округления, до насыщения) сравниваются
две модели роста: степенная (log d ~ p log t) и экспоненциальная (ln d ~ t/λ).

    python scripts/analyze_p2_divergence.py --run runs/W2-P2-1-55404980 \
        --output results/evidence/W2_T008_P2-1_divergence_20261012/analysis.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import numpy as np  # noqa: E402

YEAR = 365.25*86400.0
LIMIT = 1e-6
LOW, HIGH = 1e-13, 1e-4


def load_rows(run):
    rows = {}
    for line in (ROOT/run/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        if line.strip():
            r = json.loads(line)
            rows[(r['orbit_id'], float(r['years']), r['variant'])] = r
    return rows


def case_scales():
    from submoon_research.workflows.w2_comparison import load_cases
    _, cases = load_cases(ROOT)
    return {c['orbit_id']: dict(a=c['a_km'], period=c['period_seconds']) for c in cases}


def curve(x, y, scale):
    a, period = scale['a'], scale['period']
    na = 2*math.pi*a/period
    ys = {t: s for t, s in y['samples']}
    out = []
    for t, s in x['samples']:
        if t in ys and t > 0:
            dr = np.linalg.norm(np.subtract(s[:3], ys[t][:3]))/a
            dv = np.linalg.norm(np.subtract(s[3:], ys[t][3:]))/na
            out.append((t, float(max(dr, dv))))
    return out


def tau(points):
    return next((t for t, d in points if d > LIMIT), None)


def growth(points):
    pts = [(t, d) for t, d in points if LOW < d < HIGH]
    if len(pts) < 6:
        return None
    t = np.array([p[0] for p in pts])
    d = np.array([p[1] for p in pts])
    lt, ld = np.log10(t), np.log10(d)
    power = np.polyfit(lt, ld, 1)
    power_rms = float(np.sqrt(np.mean((np.polyval(power, lt)-ld)**2)))
    expo = np.polyfit(t, np.log(d), 1)
    expo_rms = float(np.sqrt(np.mean((np.polyval(expo, t)-np.log(d))**2))/math.log(10))
    return dict(points=len(pts), t_range_years=[float(t[0]/YEAR), float(t[-1]/YEAR)],
                power_slope=float(power[0]), power_rms_dex=power_rms,
                exp_efold_years=float(1/expo[0]/YEAR) if expo[0] > 0 else None, exp_rms_dex=expo_rms,
                better='power' if power_rms <= expo_rms else 'exponential')


def early_level(points, frac=1e-3):
    """Уровень расхождения на ранней части сетки (t <= frac H): оценка относительной точности."""
    if not points:
        return None
    horizon = points[-1][0]
    early = [d for t, d in points if t <= frac*horizon*1.0001]
    return float(np.median(early)) if early else None


def high_e(rows, years):
    out = []
    for (orbit, y, v), r in rows.items():
        if y != years or v not in 'DT':
            continue
        e = r.get('eccentricity') or {}
        nd = r.get('numerical_domain') or {}
        if not nd.get('outside_validated_domain'):
            continue
        first = e.get('first_bound_at_limit_time')
        end = r['event']['time'] if r.get('event') else r['last_valid_time']
        out.append(dict(orbit_id=orbit, variant=v, outcome=r['outcome'],
                        first_e09_years=None if first is None else first/YEAR,
                        terminal_years=end/YEAR, lead_years=None if first is None else (end-first)/YEAR,
                        approach_steps=e.get('bound_approach_steps_at_limit'), max_bound=e.get('max_bound')))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', default='runs/W2-P2-1-55404980')
    parser.add_argument('--years', type=float, default=1000.0)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    rows, scales = load_rows(args.run), case_scales()
    orbits = sorted({k[0] for k in rows})
    report = dict(run=args.run, years=args.years, limit=LIMIT, fit_window=[LOW, HIGH], orbits=[])
    for orbit in orbits:
        get = lambda v, y=args.years: rows.get((orbit, y, v))  # noqa: E731
        item = dict(orbit_id=orbit, a_km=scales[orbit]['a'], period_days=scales[orbit]['period']/86400,
                    outcomes={v: get(v)['outcome'] for v in 'DTB' if get(v)},
                    terminal_years=min(get(v)['last_valid_time'] for v in 'DTB' if get(v))/YEAR, pairs={})
        for x, y in (('D', 'T'), ('D', 'B'), ('T', 'B')):
            if not (get(x) and get(y)):
                continue
            pts = curve(get(x), get(y), scales[orbit])
            t = tau(pts)
            item['pairs'][x+'-'+y] = dict(tau_years=None if t is None else t/YEAR,
                tau_periods=None if t is None else t/scales[orbit]['period'],
                d_final=pts[-1][1] if pts else None, d_early=early_level(pts), growth=growth(pts),
                curve=[(p[0]/YEAR, p[1]) for p in pts])
        # Сверка τ(D,T) между независимыми заданиями разных горизонтов (разные сетки).
        item['tau_DT_by_horizon'] = {}
        for y in (1.0, 10.0, 100.0, 1000.0, 10000.0):
            if get('D', y) and get('T', y):
                t = tau(curve(get('D', y), get('T', y), scales[orbit]))
                item['tau_DT_by_horizon'][str(int(y))] = None if t is None else t/YEAR
        report['orbits'].append(item)
    report['high_e'] = high_e(rows, args.years)
    report['high_e_10000'] = high_e(rows, 10000.0)
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    return report


if __name__ == '__main__':
    main()
