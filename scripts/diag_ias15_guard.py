"""W2-I015: диагностика стража полинома IAS15 (движок B) после смены нормировки.

1) Синтетика: круговая орбита около начала координат, старт t0 = 2**31 с (ulp ~ 4.8e-7 с),
   600 шагов. Прежняя покомпонентная мера ожидаемо превышает 1e-9 на проходах компонент
   через ноль (подтверждение механизма); новая мера по телам — нет.
2) Реальный случай, на котором B упал в P2-1 (himalia-inner-90-0.0: отказ стража на
   1.2907 года): тот же расчёт до --years лет новым кодом должен завершиться.

Не научный расчёт и не допуск; ничего не пишет вне --output.

    ~/venvs/submoon-w2/bin/python scripts/diag_ias15_guard.py --output scratch/claude/W2-I015_diag.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import numpy as np  # noqa: E402

from submoon_research.dynamics import native_ias15 as nm  # noqa: E402

YEAR = 365.25*86400.0
LIMIT = nm.ENDPOINT_RESIDUAL_LIMIT
TRACK = dict(component_max=0.0, component_over_limit=0, body_max=0.0, steps=0)
_original_step = nm.NativeIAS15.step


def _tracked_step(self, *args, **kwargs):
    segment = _original_step(self, *args, **kwargs)
    TRACK['steps'] += 1
    TRACK['component_max'] = max(TRACK['component_max'], self.last_component_residual)
    TRACK['component_over_limit'] += int(self.last_component_residual > LIMIT)
    TRACK['body_max'] = max(TRACK['body_max'], self.last_endpoint_residual)
    return segment


nm.NativeIAS15.step = _tracked_step


def reset():
    TRACK.update(component_max=0.0, component_over_limit=0, body_max=0.0, steps=0)


def synthetic(steps=600):
    reset()
    gm, radius = 1.26686534e8, 1.0e5
    state = np.array([[0, 0, 0, 0, 0, 0], [radius, 0, 0, 0, math.sqrt(gm/radius), 0]], dtype=float)
    native = nm.NativeIAS15(state, [gm], [], t0=float(2**31), initial_dt=10.0)
    error = None
    try:
        for _ in range(steps):
            native.step(native.sim.t+1.0e6)
    except ArithmeticError as exc:
        error = str(exc)
    return dict(TRACK, error=error, t0=float(2**31))


def real_case(orbit_id, years):
    from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine
    from submoon_research.workflows.w2_comparison import load_cases

    reset()
    _, cases = load_cases(ROOT)
    case = next(c for c in cases if c['orbit_id'] == orbit_id)
    started = time.perf_counter()
    out = integrate_engine('ias15_reboundx', case['state'], case['gms'], case['figures'], case['radii'],
                           years*YEAR, period=case['period_seconds'], mode=NumericMode(),
                           hill_km=case.get('hill_km'), escape_window=case.get('escape_window'))
    return dict(TRACK, orbit_id=orbit_id, years=years, run_status=out['run_status'], error=out['error'],
                outcome=out['physical_outcome'], last_valid_years=out['last_valid_time']/YEAR,
                endpoint_residual_max=out['endpoint_residual'], wall=time.perf_counter()-started)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--case', default='himalia-inner-90-0.0')
    parser.add_argument('--years', type=float, default=1.5)
    parser.add_argument('--skip-real', action='store_true')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    report = dict(issue='W2-I015', limit=LIMIT, synthetic=synthetic())
    s = report['synthetic']
    print(f"синтетика (t0=2^31 с, {s['steps']} шагов): новая мера max {s['body_max']:.2e}; прежняя "
          f"max {s['component_max']:.2e}, шагов > {LIMIT:g}: {s['component_over_limit']}; ошибка: {s['error']}", flush=True)
    ok = s['error'] is None and s['body_max'] < LIMIT
    if not args.skip_real:
        r = real_case(args.case, args.years)
        report['real'] = r
        print(f"{r['orbit_id']} B до {r['years']:g} лет: {r['run_status']} ({r['outcome']}), дошёл до "
              f"{r['last_valid_years']:.4f} лет за {r['wall']:.0f} с; новая мера max {r['body_max']:.2e}; "
              f"прежняя max {r['component_max']:.2e}, шагов > {LIMIT:g}: {r['component_over_limit']}; "
              f"ошибка: {r['error']}", flush=True)
        ok = ok and r['run_status'] == 'completed'
    report['mechanism_confirmed'] = bool(s['component_over_limit'] or report.get('real', {}).get('component_over_limit'))
    report['fix_ok'] = ok
    print(f"механизм (прежняя мера > {LIMIT:g}) подтверждён: {report['mechanism_confirmed']}; исправление: "
          f"{'OK' if ok else 'НЕ ПОДТВЕРЖДЕНО'}")
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
