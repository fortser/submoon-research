"""W2-P004, ступень P1: эквивалентность прототипа скомпилированного DOP853 и scipy-пути A.

Критерии зафиксированы заранее в docs/tasks/W2_compiled_mode_admission_v1.md §2:
  P1-C1 одинаковые run_status, physical_outcome, тип события и тело — 100%;
  P1-C2 |Δt| события / конца интеграции <= 1e-6 P0;
  P1-C3 |Δr|/a и |Δv|/(na) пробы в общих точках выборки <= 1e-6 (горизонты 20 и 200 P0);
  P1-C4 число шагов и вычислений силы — расхождение <= 1% (диагностика);
  P1-D1 время первого превышения |Δr|/a > 1e-6 — только отчёт (2000 P0).
Набор: 48 разработочных случаев load_cases (не вероятностная выборка). Пул spawn.
Результат — проверка реализации, не допуск точности режима (это ступень P2).

    ~/venvs/submoon-w2/bin/python scripts/admission_p1_equivalence.py --workers 6
"""
from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
import time
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
from submoon_research.workflows.w2_comparison import load_cases, warmup  # noqa: E402

TRAJECTORY_LIMIT = 1e-6
TIME_LIMIT_PERIODS = 1e-6
COUNT_LIMIT = 0.01
STRICT_HORIZONS = (20.0, 200.0)


def _init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


def _job(job):
    case, periods, engine = job['case'], job['periods'], job['engine']
    horizon = case['period_seconds']*periods
    kwargs = dict(period=case['period_seconds'], mode=NumericMode(), hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=np.linspace(0, horizon, 65))
    started = time.perf_counter()
    if engine == 'compiled':
        out = integrate_compiled(case['state'], case['gms'], case['figures'], case['radii'],
                                 horizon, **kwargs)
    else:
        out = integrate_engine('dop853_jit', case['state'], case['gms'], case['figures'],
                               case['radii'], horizon, **kwargs)
    event = out['event']
    return dict(orbit_id=case['orbit_id'], periods=periods, engine=engine,
        wall=time.perf_counter()-started, run_status=out['run_status'], error=out['error'],
        outcome=out['physical_outcome'], last_valid_time=out['last_valid_time'],
        event=None if event is None else dict(event=event['event'], time=event['time'],
                                              body=event.get('body_index')),
        steps=out['steps'], nfev=out['nfev'], python_event_steps=out.get('python_event_steps'),
        final_probe=out['final_state'][-6:],
        samples=[(s['time'], s['state'][-6:]) for s in out['samples']])


def compare(ref, new, case):
    period, a = case['period_seconds'], case['a_km']
    na = 2*math.pi*a/period
    row = dict(orbit_id=case['orbit_id'], host=case['host'], radial=case.get('radial'),
               periods=ref['periods'], outcome_ref=ref['outcome'], outcome_new=new['outcome'],
               status=(ref['run_status'], new['run_status']), errors=(ref['error'], new['error']))
    same_event = (ref['event'] is None) == (new['event'] is None)
    if same_event and ref['event']:
        same_event = (ref['event']['event'] == new['event']['event']
                      and ref['event']['body'] == new['event']['body'])
    row['C1'] = (ref['run_status'] == new['run_status'] == 'completed'
                 and ref['outcome'] == new['outcome'] and same_event)
    dt = abs(new['last_valid_time']-ref['last_valid_time'])/period
    if ref['event'] and new['event']:
        dt = max(dt, abs(new['event']['time']-ref['event']['time'])/period)
    row['dt_periods'] = dt
    row['C2'] = dt <= TIME_LIMIT_PERIODS
    pairs = {t: s for t, s in ref['samples']}
    dr, dv, diverged = 0.0, 0.0, None
    for t, s in new['samples']:
        if t in pairs:
            r = float(np.linalg.norm(np.subtract(s[:3], pairs[t][:3]))/a)
            v = float(np.linalg.norm(np.subtract(s[3:], pairs[t][3:]))/na)
            dr, dv = max(dr, r), max(dv, v)
            if diverged is None and max(r, v) > TRAJECTORY_LIMIT:
                diverged = t/period
    if row['C2'] and abs(new['last_valid_time']-ref['last_valid_time']) == 0.0:
        r = float(np.linalg.norm(np.subtract(new['final_probe'][:3], ref['final_probe'][:3]))/a)
        v = float(np.linalg.norm(np.subtract(new['final_probe'][3:], ref['final_probe'][3:]))/na)
        dr, dv = max(dr, r), max(dv, v)
        if diverged is None and max(r, v) > TRAJECTORY_LIMIT:
            diverged = new['last_valid_time']/period
    row.update(max_dr_over_a=dr, max_dv_over_na=dv, first_exceedance_periods=diverged,
               compared_samples=sum(1 for t, _ in new['samples'] if t in pairs))
    row['C3'] = (dr <= TRAJECTORY_LIMIT and dv <= TRAJECTORY_LIMIT) if ref['periods'] in STRICT_HORIZONS else None
    row['steps'] = (ref['steps'], new['steps'])
    row['nfev'] = (ref['nfev'], new['nfev'])
    row['C4'] = (abs(ref['steps']-new['steps']) <= COUNT_LIMIT*max(ref['steps'], 1)
                 and abs(ref['nfev']-new['nfev']) <= COUNT_LIMIT*max(ref['nfev'], 1))
    row['python_event_steps'] = new['python_event_steps']
    row['wall'] = (ref['wall'], new['wall'])
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--periods', type=float, nargs='*', default=[20.0, 200.0, 2000.0])
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    _, cases = load_cases(ROOT)
    cases = cases[:args.limit]
    by_id = {c['orbit_id']: c for c in cases}
    info = cpu_info()
    print(f"P1: прототип v{PROTOTYPE_VERSION}; случаев {len(cases)}; горизонты {args.periods} P0; "
          f"процессов {args.workers}; CPU {info.get('lscpu', {}).get('Model name', '?')}", flush=True)
    jobs = [dict(case=c, periods=p, engine=e) for p in args.periods for c in cases
            for e in ('scipy_A', 'compiled')]
    jobs.sort(key=lambda j: (j['engine'] != 'scipy_A', -j['periods']))  # длинные scipy первыми
    started = time.perf_counter()
    with mp.get_context('spawn').Pool(args.workers, initializer=_init) as pool:
        results = []
        for k, res in enumerate(pool.imap_unordered(_job, jobs, chunksize=1), 1):
            results.append(res)
            if k % 24 == 0 or k == len(jobs):
                print(f'  выполнено {k}/{len(jobs)} заданий, {time.perf_counter()-started:.0f} с', flush=True)
    index = {(r['orbit_id'], r['periods'], r['engine']): r for r in results}
    rows = [compare(index[(c['orbit_id'], p, 'scipy_A')], index[(c['orbit_id'], p, 'compiled')], c)
            for p in args.periods for c in cases]

    print()
    verdict = True
    for p in args.periods:
        sub = [r for r in rows if r['periods'] == p]
        c1 = sum(r['C1'] for r in sub)
        c2 = sum(r['C2'] for r in sub)
        c3 = [r['C3'] for r in sub if r['C3'] is not None]
        c4 = sum(r['C4'] for r in sub)
        div = [r for r in sub if r['first_exceedance_periods'] is not None]
        t_ref = sum(r['wall'][0] for r in sub)
        t_new = sum(r['wall'][1] for r in sub)
        print(f'{p:g} P0: C1 {c1}/{len(sub)}, C2 {c2}/{len(sub)}, '
              + (f'C3 {sum(c3)}/{len(c3)}, ' if c3 else 'C3 — (только отчёт), ')
              + f'C4 {c4}/{len(sub)}; max |Δr|/a {max(r["max_dr_over_a"] for r in sub):.1e}, '
              f'max |Δt| {max(r["dt_periods"] for r in sub):.1e} P0; превышение 1e-6: {len(div)} случаев; '
              f'время scipy {t_ref:.0f} с / прототип {t_new:.1f} с (сумма заданий)')
        verdict &= c1 == len(sub) and c2 == len(sub) and (not c3 or all(c3))
        for r in sub:
            if not (r['C1'] and r['C2'] and r['C3'] is not False):
                print(f"   НАРУШЕНИЕ {r['orbit_id']}: исход {r['outcome_ref']}/{r['outcome_new']}, "
                      f"статус {r['status']}, |Δt| {r['dt_periods']:.1e} P0, |Δr|/a {r['max_dr_over_a']:.1e}, "
                      f"|Δv|/na {r['max_dv_over_na']:.1e}, ошибки {r['errors']}")
        for r in div:
            print(f"   расхождение {r['orbit_id']}: впервые > 1e-6 на {r['first_exceedance_periods']:.0f} P0, "
                  f"исход {r['outcome_ref']} (совпал: {r['C1']})")
    print('\nИТОГ P1 (C1, C2 на всех горизонтах; C3 на 20 и 200 P0):', 'ПРОЙДЕНО' if verdict else 'НЕ ПРОЙДЕНО')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-P004_P1_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(plan='W2-P004', stage='P1', criteria_doc='docs/tasks/W2_compiled_mode_admission_v1.md',
        utc=stamp, prototype_version=PROTOTYPE_VERSION, machine=info, periods=args.periods,
        cases=list(by_id), numeric=NumericMode().__dict__, verdict_passed=verdict,
        wall_seconds=time.perf_counter()-started, rows=rows), ensure_ascii=False, indent=1, default=float),
        encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0 if verdict else 1


if __name__ == '__main__':
    raise SystemExit(main())
