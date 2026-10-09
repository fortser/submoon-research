"""W2-P004, ступень P1: эквивалентность прототипа скомпилированного DOP853 и scipy-пути A.

Критерии зафиксированы заранее в docs/tasks/W2_compiled_mode_admission_v1.md §2:
  P1-C1 одинаковые run_status, physical_outcome, тип события и тело — 100%;
  P1-C2 |Δt| события / конца интеграции <= 1e-6 P0;
  P1-C3 |Δr|/a и |Δv|/(na) пробы в общих точках выборки <= 1e-6 (горизонты 20 и 200 P0);
  P1-C4 число шагов и вычислений силы — расхождение <= 1% (диагностика);
  P1-D1 время первого превышения |Δr|/a > 1e-6 — только отчёт (2000 P0).
Набор: 48 разработочных случаев load_cases (не вероятностная выборка). Пул spawn.
Результат — проверка реализации, не допуск точности режима (это ступень P2).

Версия 2 оценщика (W2-I010, W2-I012): сетка выборок дополнена геометрическими точками от
1e-4 H, поэтому участок до раннего события покрыт; C3 требует не менее MIN_COMMON общих
точек строго до первого конца, иначе — inconclusive; конечные состояния (в том числе в
момент события) сравниваются всегда, с допуском на сдвиг времени |v|·|Δt| и (GM/r²)·|Δt|;
итог «пройдено» возможен только для полного ожидаемого набора случаев и горизонтов.

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
FULL_PERIODS = (20.0, 200.0, 2000.0)
FULL_CASE_COUNT = 48
MIN_COMMON = 8
EVALUATOR_VERSION = 2


def sample_grid(horizon):
    return np.unique(np.concatenate([np.linspace(0, horizon, 65),
                                     np.geomspace(1e-4*horizon, horizon, 128)]))


def _init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


def _job(job):
    case, periods, engine = job['case'], job['periods'], job['engine']
    horizon = case['period_seconds']*periods
    kwargs = dict(period=case['period_seconds'], mode=NumericMode(), hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=sample_grid(horizon))
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


def _state_difference(a_probe, b_probe, dt_seconds, gm_host, a, na):
    """|Δr|/a и |Δv|/(na) за вычетом допустимого вклада сдвига времени Δt."""
    a_probe, b_probe = np.asarray(a_probe, float), np.asarray(b_probe, float)
    r = max(np.linalg.norm(a_probe[:3]), np.linalg.norm(b_probe[:3]))
    v = max(np.linalg.norm(a_probe[3:]), np.linalg.norm(b_probe[3:]))
    dr = np.linalg.norm(a_probe[:3]-b_probe[:3]) - v*dt_seconds
    dv = np.linalg.norm(a_probe[3:]-b_probe[3:]) - gm_host/r**2*dt_seconds
    return max(float(dr), 0.0)/a, max(float(dv), 0.0)/na


def compare(ref, new, case):
    period, a = case['period_seconds'], case['a_km']
    na = 2*math.pi*a/period
    gm_host = float(case['gms'][0]) if case.get('gms') is not None else (2*math.pi/period)**2*a**3
    row = dict(orbit_id=case['orbit_id'], host=case.get('host'), radial=case.get('radial'),
               periods=ref['periods'], outcome_ref=ref['outcome'], outcome_new=new['outcome'],
               status=(ref['run_status'], new['run_status']), errors=(ref['error'], new['error']),
               evaluator_version=EVALUATOR_VERSION)
    same_event = (ref['event'] is None) == (new['event'] is None)
    if same_event and ref['event']:
        same_event = (ref['event']['event'] == new['event']['event']
                      and ref['event']['body'] == new['event']['body'])
    row['C1'] = (ref['run_status'] == new['run_status'] == 'completed'
                 and ref['outcome'] == new['outcome'] and same_event)
    dt_seconds = abs(new['last_valid_time']-ref['last_valid_time'])
    if ref['event'] and new['event']:
        dt_seconds = max(dt_seconds, abs(new['event']['time']-ref['event']['time']))
    dt = dt_seconds/period
    row['dt_periods'] = dt
    row['C2'] = dt <= TIME_LIMIT_PERIODS
    first_end = min(ref['last_valid_time'], new['last_valid_time'])
    pairs = {t: s for t, s in ref['samples']}
    dr, dv, diverged, common = 0.0, 0.0, None, 0
    for t, s in new['samples']:
        if t in pairs and t < first_end:
            common += t > 0
            r = float(np.linalg.norm(np.subtract(s[:3], pairs[t][:3]))/a)
            v = float(np.linalg.norm(np.subtract(s[3:], pairs[t][3:]))/na)
            dr, dv = max(dr, r), max(dv, v)
            if diverged is None and max(r, v) > TRAJECTORY_LIMIT:
                diverged = t/period
    # Конечные состояния (конец горизонта или момент события) сравниваются всегда.
    end_dr, end_dv = _state_difference(new['final_probe'], ref['final_probe'], dt_seconds,
                                       gm_host, a, na)
    dr, dv = max(dr, end_dr), max(dv, end_dv)
    if diverged is None and max(end_dr, end_dv) > TRAJECTORY_LIMIT:
        diverged = first_end/period
    row.update(max_dr_over_a=dr, max_dv_over_na=dv, end_dr_over_a=end_dr, end_dv_over_na=end_dv,
               first_exceedance_periods=diverged, compared_samples_before_end=common)
    if ref['periods'] in STRICT_HORIZONS:
        if dr > TRAJECTORY_LIMIT or dv > TRAJECTORY_LIMIT:
            row['C3'] = False
        elif common < MIN_COMMON:
            row['C3'] = 'inconclusive'
        else:
            row['C3'] = True
    else:
        row['C3'] = None
    row['steps'] = (ref['steps'], new['steps'])
    row['nfev'] = (ref['nfev'], new['nfev'])
    row['C4'] = (abs(ref['steps']-new['steps']) <= COUNT_LIMIT*max(ref['steps'], 1)
                 and abs(ref['nfev']-new['nfev']) <= COUNT_LIMIT*max(ref['nfev'], 1))
    row['python_event_steps'] = new['python_event_steps']
    row['wall'] = (ref['wall'], new['wall'])
    return row


def evaluate(rows, case_ids, periods, full_scope):
    """Итог P1. «Пройдено» — только при полном ожидаемом наборе и всех критериях;
    неполнота или inconclusive дают не-пройдено с указанием причины."""
    problems = []
    if not rows or not case_ids or not periods:
        problems.append('пустой набор')
    expected = {(c, p) for c in case_ids for p in periods}
    present = [(r['orbit_id'], r['periods']) for r in rows]
    if len(present) != len(set(present)):
        problems.append('повторяющиеся строки')
    if set(present) != expected:
        problems.append(f'неполный набор: {len(expected - set(present))} строк отсутствуют')
    if not full_scope:
        problems.append(f'сокращённый разработочный объём (нужно {FULL_CASE_COUNT} случаев и горизонты {FULL_PERIODS})')
    for r in rows:
        if not r['C1'] or not r['C2'] or r['C3'] is False:
            problems.append(f"нарушение {r['orbit_id']} {r['periods']:g} P0")
        elif r['C3'] == 'inconclusive':
            problems.append(f"inconclusive C3 {r['orbit_id']} {r['periods']:g} P0")
    return dict(passed=not problems, problems=problems)


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
    full_scope = (args.limit is None and len(cases) == FULL_CASE_COUNT
                  and tuple(sorted(args.periods)) == FULL_PERIODS)
    for p in args.periods:
        sub = [r for r in rows if r['periods'] == p]
        c1 = sum(r['C1'] for r in sub)
        c2 = sum(r['C2'] for r in sub)
        c3 = [r['C3'] is True for r in sub if r['C3'] is not None]
        c4 = sum(r['C4'] for r in sub)
        div = [r for r in sub if r['first_exceedance_periods'] is not None]
        t_ref = sum(r['wall'][0] for r in sub)
        t_new = sum(r['wall'][1] for r in sub)
        print(f'{p:g} P0: C1 {c1}/{len(sub)}, C2 {c2}/{len(sub)}, '
              + (f'C3 {sum(c3)}/{len(c3)}, ' if c3 else 'C3 — (только отчёт), ')
              + f'C4 {c4}/{len(sub)}; max |Δr|/a {max(r["max_dr_over_a"] for r in sub):.1e}, '
              f'max |Δt| {max(r["dt_periods"] for r in sub):.1e} P0; превышение 1e-6: {len(div)} случаев; '
              f'время scipy {t_ref:.0f} с / прототип {t_new:.1f} с (сумма заданий)')
        for r in sub:
            if not (r['C1'] and r['C2'] and r['C3'] in (True, None)):
                print(f"   НАРУШЕНИЕ {r['orbit_id']}: исход {r['outcome_ref']}/{r['outcome_new']}, "
                      f"статус {r['status']}, |Δt| {r['dt_periods']:.1e} P0, |Δr|/a {r['max_dr_over_a']:.1e}, "
                      f"|Δv|/na {r['max_dv_over_na']:.1e}, точек до конца {r['compared_samples_before_end']}, "
                      f"C3 {r['C3']}, ошибки {r['errors']}")
        for r in div:
            print(f"   расхождение {r['orbit_id']}: впервые > 1e-6 на {r['first_exceedance_periods']:.0f} P0, "
                  f"исход {r['outcome_ref']} (совпал: {r['C1']})")
    result = evaluate(rows, [c['orbit_id'] for c in cases], args.periods, full_scope)
    verdict = result['passed']
    print('\nИТОГ P1 (C1, C2 на всех горизонтах; C3 на 20 и 200 P0; полный набор):',
          'ПРОЙДЕНО' if verdict else 'НЕ ПРОЙДЕНО')
    for problem in result['problems'][:20]:
        print('  -', problem)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-P004_P1_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(plan='W2-P004', stage='P1', criteria_doc='docs/tasks/W2_compiled_mode_admission_v1.md',
        utc=stamp, prototype_version=PROTOTYPE_VERSION, machine=info, periods=args.periods,
        cases=list(by_id), numeric=NumericMode().__dict__, verdict_passed=verdict,
        evaluator_version=EVALUATOR_VERSION, full_scope=full_scope, problems=result['problems'],
        wall_seconds=time.perf_counter()-started, rows=rows), ensure_ascii=False, indent=1, default=float),
        encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    only_scope = (not verdict and not full_scope
                  and all(x.startswith('сокращённый') for x in result['problems']))
    if only_scope:
        print('  (сокращённый объём: нарушений не найдено, но это не допуск P1)')
    return 0 if verdict or only_scope else 1


if __name__ == '__main__':
    raise SystemExit(main())
