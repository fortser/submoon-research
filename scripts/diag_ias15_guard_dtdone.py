"""W2-I017 (P2-1, 1000 лет): проверка гипотезы ulp(t) и исправления стража (полином на dt_last_done).

На каждом шаге IAS15 считаются два остатка стража, без отказа:
  label — полином собран на метке right-left (как в текущем коде);
  done  — полином собран на фактическом шаге sim.dt_last_done (предлагаемое исправление).
Старт реального случая сдвигается в t0 (система автономна: J2 с неподвижным полюсом), поэтому
ulp(t) больших времён воспроизводится за минуты без 1000-летнего прогона.

Ожидание при верной гипотезе: label растёт ~ ulp(t0) (x2 на каждой степени двойки; для
ganymede-inner около 1e-9 за 2**34 с = 544.4 года), done остаётся на дне ~1e-14 при любом t0.
Не научный расчёт и не допуск; работает с текущим src (патч не нужен), пишет только --output.

    ~/venvs/submoon-w2/bin/python scripts/diag_ias15_guard_dtdone.py \
        --output scratch/claude/W2-I017_dtdone.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import numpy as np  # noqa: E402

from submoon_research.dynamics.engine_compare import NumericMode  # noqa: E402
from submoon_research.dynamics.native_ias15 import (ENDPOINT_RESIDUAL_LIMIT, NativeIAS15,  # noqa: E402
                                                     endpoint_residual)
from submoon_research.workflows.w2_comparison import load_cases  # noqa: E402

YEAR = 365.25*86400.0


def probe(case, t0, steps):
    period = case['period_seconds']
    native = NativeIAS15(np.asarray(case['state'], dtype=float), case['gms'], case['figures'],
                         epsilon=NumericMode().epsilon, t0=t0, initial_dt=period*1e-5)
    if not hasattr(native.sim, 'dt_last_done'):
        raise RuntimeError('В этой сборке REBOUND нет sim.dt_last_done')
    label_max = done_max = 0.0
    label_over = done_over = 0
    mismatch_max = 0.0  # |dt_done - (right-left)| / spacing(right)
    started = time.perf_counter()
    for _ in range(steps):
        left = float(native.sim.t)
        start = native.absolute_state()
        native.sim.steps(1)
        right = float(native.sim.t)
        dt_label, dt_done = right-left, float(native.sim.dt_last_done)
        a0 = native._array('a0', 3*native.count).reshape(native.count, 3)
        b = native._array('br', 21*native.count).reshape(7, native.count, 3)
        end = native.absolute_state()
        r_label = endpoint_residual(native._assemble(start, a0, b, dt_label).sum(axis=0), end)
        r_done = endpoint_residual(native._assemble(start, a0, b, dt_done).sum(axis=0), end)
        label_max, done_max = max(label_max, r_label), max(done_max, r_done)
        label_over += int(r_label > ENDPOINT_RESIDUAL_LIMIT)
        done_over += int(r_done > ENDPOINT_RESIDUAL_LIMIT)
        mismatch_max = max(mismatch_max, abs(dt_done-dt_label)/np.spacing(right))
    return dict(t0_seconds=t0, t0_years=t0/YEAR, ulp_t0=float(np.spacing(t0)) if t0 else 0.0,
                steps=steps, simulated_years=(float(native.sim.t)-t0)/YEAR,
                label_max=label_max, label_over_limit=label_over,
                done_max=done_max, done_over_limit=done_over,
                dt_mismatch_max_in_ulp=mismatch_max, wall=time.perf_counter()-started)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--case', default='ganymede-inner-180-0.0')
    parser.add_argument('--t0-years', type=float, nargs='+', default=[0, 100, 300, 600, 1000, 10000])
    parser.add_argument('--steps', type=int, default=30000)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    _, cases = load_cases(ROOT)
    case = next(c for c in cases if c['orbit_id'] == args.case)
    report = dict(issue='W2-I017', limit=ENDPOINT_RESIDUAL_LIMIT, case=args.case, rows=[])
    print(f"{'t0, лет':>9} {'ulp(t0), с':>11} {'label max':>10} {'>порога':>8} {'done max':>10} {'>порога':>8} {'|Δdt|/ulp':>9}")
    for years in args.t0_years:
        row = probe(case, years*YEAR, args.steps)
        report['rows'].append(row)
        print(f"{years:9g} {row['ulp_t0']:11.2e} {row['label_max']:10.2e} {row['label_over_limit']:8d} "
              f"{row['done_max']:10.2e} {row['done_over_limit']:8d} {row['dt_mismatch_max_in_ulp']:9.2f}", flush=True)
    rows = report['rows']
    report['hypothesis_ulp_confirmed'] = bool(rows[-1]['label_max'] > 100*rows[0]['label_max'])
    report['fix_ok'] = all(r['done_over_limit'] == 0 and r['done_max'] < 1e-12 for r in rows)
    print(f"гипотеза ulp(t) (label растёт с t0): {report['hypothesis_ulp_confirmed']}; "
          f"исправление (done < 1e-12 при всех t0): {'OK' if report['fix_ok'] else 'НЕ ПОДТВЕРЖДЕНО'}")
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0 if report['fix_ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
