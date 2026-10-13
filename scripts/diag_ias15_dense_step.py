"""W2-I019: ошибка плотного вывода B (IAS15) внутри шага в зависимости от ограничения шага.

Для каждой орбиты и ограничения шага (доля P0; none — как в P2-1) B считается дважды:
естественными шагами (состояния в моментах сетки — из полинома шага, как в стенде) и шагами,
оканчивающимися точно в моментах сетки (без интерполяции). Разница — ошибка интерполяции;
вторая траектория сравнивается и с D из run P2-1 (та же сетка grid(H) стенда). Меры — как в
критерии τ: max(|Δr|/a, |Δv|/(n a)). Не научный расчёт; пишет только --output.

    python scripts/diag_ias15_dense_step.py --output results/evidence/W2_T015_b_step_20261012/diag.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import numpy as np  # noqa: E402

from submoon_research.dynamics.engine_compare import NumericMode  # noqa: E402
from submoon_research.dynamics.native_ias15 import NativeIAS15  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases  # noqa: E402

YEAR = 365.25*86400.0
DEFAULT = [('himalia-outer-90-0.0', 1.0, 0.16), ('himalia-outer-180-0.0', 1.0, 0.16),
           ('himalia-outer-0-0.0', 1.0, 0.2), ('himalia-outer-90-0.0-phase', 1.0, 0.4),
           ('iapetus-outer-180-0.0', 1.0, 1.0), ('ganymede-outer-180-0.0', 1.0, 1.0),
           ('ganymede-inner-0-0.0', 1.0, 0.03), ('iapetus-inner-0-0.3', 1.0, 0.03)]


def grid(horizon):
    spec = importlib.util.spec_from_file_location('adm', ROOT/'scripts'/'admission_p2_horizons.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.grid(horizon)


def measure(case, years, until, cap, d_rows):
    horizon, period, a = years*YEAR, case['period_seconds'], case['a_km']
    na = 2*math.pi*a/period
    times = [t for t in grid(horizon) if 0 < t <= until*YEAR]
    gm_host = float(case['gms'][0])

    def limit(n):
        # cap — доля P0 (число) или 'local:k' — k·min(P0, 2π·sqrt(r³/GM хозяина)) по текущему r пробы.
        if cap is None:
            return np.inf
        if isinstance(cap, str):
            k = float(cap.split(':')[1])
            s = n.absolute_state()
            r = float(np.linalg.norm(s[-1, :3]-s[0, :3]))
            return k*min(period, 2*math.pi*math.sqrt(r**3/gm_host))
        return cap*period

    def native():
        return NativeIAS15(np.asarray(case['state'], dtype=float), case['gms'], case['figures'],
                           epsilon=NumericMode().epsilon, t0=0.0, initial_dt=min(period*1e-5, horizon))
    started = time.perf_counter()
    n, segments = native(), []
    while n.sim.t < times[-1]:
        segments.append(n.step(horizon, limit(n)))
    wall = time.perf_counter()-started
    m, exact, seg = native(), {}, None
    for t in times:
        while m.sim.t < t:
            seg = m.step(t, limit(m))
        exact[t] = np.asarray(seg(seg.right)).reshape(-1, 6)[-1]
    index = 0

    def dense(t):
        nonlocal index
        while segments[index].right < t:
            index += 1
        return np.asarray(segments[index](t)).reshape(-1, 6)[-1]

    def dist(x, y):
        return max(float(np.linalg.norm(x[:3]-y[:3])/a), float(np.linalg.norm(x[3:]-y[3:])/na))
    interp = [dist(dense(t), exact[t]) for t in times]
    vs_d = [dist(exact[t], np.asarray(d_rows[t])) for t in times if t in d_rows]
    steps = np.diff([s.left for s in segments] + [segments[-1].right])
    return dict(cap_periods=cap, steps=len(segments), steps_per_year=len(segments)/(times[-1]/YEAR),
                median_step_periods=float(np.median(steps)/period), max_step_periods=float(steps.max()/period),
                interp_max=max(interp), interp_median=float(np.median(interp)),
                exactB_vs_D_max=max(vs_d) if vs_d else None, wall=wall)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--caps', nargs='+', default=['none', '0.05', '0.025', 'local:0.025'])
    parser.add_argument('--orbits', nargs='*', default=None)
    parser.add_argument('--run', default='runs/W2-P2-1-55404980')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    warnings.filterwarnings('ignore')
    _, cases = load_cases(ROOT)
    by = {c['orbit_id']: c for c in cases}
    rows = {}
    for line in (ROOT/args.run/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        r = json.loads(line)
        if r['variant'] == 'D':
            rows[(r['orbit_id'], r['years'])] = dict((t, s) for t, s in r['samples'])
    plan = [p for p in DEFAULT if args.orbits is None or p[0] in args.orbits]
    report = dict(issue='W2-I019', run=args.run, results=[])
    print(f"{'орбита':28} {'шаг ≤':>6} {'шагов/год':>10} {'медиана P0':>10} {'макс P0':>8} {'интерп. max':>11} {'B−D max':>9}")
    for orbit, years, until in plan:
        for cap_text in args.caps:
            cap = None if cap_text == 'none' else cap_text if cap_text.startswith('local:') else float(cap_text)
            res = measure(by[orbit], years, until, cap, rows.get((orbit, years), {}))
            res.update(orbit_id=orbit, years=years, until_years=until)
            report['results'].append(res)
            print(f"{orbit:28} {cap_text:>6} {res['steps_per_year']:10.0f} {res['median_step_periods']:10.4f} "
                  f"{res['max_step_periods']:8.4f} {res['interp_max']:11.2e} "
                  f"{res['exactB_vs_D_max'] if res['exactB_vs_D_max'] is None else format(res['exactB_vs_D_max'], '9.2e')}",
                  flush=True)
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
