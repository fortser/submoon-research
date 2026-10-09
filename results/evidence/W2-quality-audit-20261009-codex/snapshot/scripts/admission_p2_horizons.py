"""W2-P004, ступень P2-1: точность режима на ступенях горизонта 1–1000 лет.

Критерии зафиксированы до расчёта: docs/tasks/W2_compiled_mode_admission_v1.md §3.1.
Варианты: D — прототип NumericMode(); T — прототип NumericMode.tight();
B — IAS15/REBOUNDx (только до --b-max-years). Пары D–T, D–B, T–B.
P2-Q1: все прогоны completed. P2-Q2: если событие наступает до времени расхождения
τ (|Δr|/a или |Δv|/(na) > 1e-6) или τ нет — тот же исход/тип/тело и |Δt| <= 1e-6 P0.
Остальное — отчёт. Ступени идут по порядку; следующая — только после пройденной.

Каждое завершённое задание сразу дописывается в jobs.jsonl каталога запуска;
повторный запуск продолжает с места остановки (задания той же версии прототипа).

    ~/venvs/submoon-w2/bin/python scripts/admission_p2_horizons.py --workers 6
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

YEAR = 365.25*86400.0
LIMIT = 1e-6
PAIRS = (('D', 'T'), ('D', 'B'), ('T', 'B'))


def grid(horizon):
    return np.unique(np.concatenate([[0.0], np.linspace(0, horizon, 65),
                                     np.geomspace(1e-4*horizon, horizon, 128)]))


def _init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


def _job(job):
    case, years, variant = job['case'], job['years'], job['variant']
    horizon = years*YEAR
    mode = NumericMode.tight() if variant == 'T' else NumericMode()
    kwargs = dict(period=case['period_seconds'], mode=mode, hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=grid(horizon))
    started = time.perf_counter()
    if variant == 'B':
        out = integrate_engine('ias15_reboundx', case['state'], case['gms'], case['figures'],
                               case['radii'], horizon, **kwargs)
    else:
        out = integrate_compiled(case['state'], case['gms'], case['figures'], case['radii'],
                                 horizon, **kwargs)
    event = out['event']
    return dict(orbit_id=case['orbit_id'], years=years, variant=variant,
        prototype_version=PROTOTYPE_VERSION, wall=time.perf_counter()-started,
        run_status=out['run_status'], error=out['error'], outcome=out['physical_outcome'],
        last_valid_time=out['last_valid_time'],
        event=None if event is None else dict(event=event['event'], time=event['time'],
                                              body=event.get('body_index')),
        steps=out['steps'], nfev=out['nfev'], eccentricity=out.get('eccentricity_monitor'),
        final_probe=out['final_state'][-6:],
        samples=[(s['time'], s['state'][-6:]) for s in out['samples']])


def compare(x, y, case):
    period, a = case['period_seconds'], case['a_km']
    na = 2*math.pi*a/period
    ys = {t: s for t, s in y['samples']}
    tau, dr_max, dv_max = None, 0.0, 0.0
    for t, s in x['samples']:
        if t not in ys:
            continue
        dr = float(np.linalg.norm(np.subtract(s[:3], ys[t][:3]))/a)
        dv = float(np.linalg.norm(np.subtract(s[3:], ys[t][3:]))/na)
        if tau is None:
            dr_max, dv_max = max(dr_max, dr), max(dv_max, dv)
            if max(dr, dv) > LIMIT:
                tau = t
    times = [e['time'] for e in (x['event'], y['event']) if e]
    first_event = min(times) if times else None
    checked = first_event is not None and (tau is None or first_event < tau)
    same = (x['outcome'] == y['outcome'] and (x['event'] is None) == (y['event'] is None)
            and (x['event'] is None or (x['event']['event'] == y['event']['event']
                                        and x['event']['body'] == y['event']['body'])))
    dt = (abs(x['event']['time']-y['event']['time'])/period
          if x['event'] and y['event'] else None)
    q2 = True
    if checked:
        q2 = same and dt is not None and dt <= LIMIT
    elif first_event is None and tau is None:
        q2 = same
    return dict(tau_seconds=tau, tau_periods=None if tau is None else tau/period,
                tau_years=None if tau is None else tau/YEAR, max_dr_before_tau=dr_max,
                max_dv_before_tau=dv_max, event_checked_before_tau=checked, same_outcome=same,
                dt_periods=dt, Q2=q2, outcomes=(x['outcome'], y['outcome']))


def evaluate(level_rows, cases, years):
    by = {(r['orbit_id'], r['variant']): r for r in level_rows}
    q1 = [r for r in level_rows if r['run_status'] != 'completed']
    pairs = []
    for case in cases:
        for a, b in PAIRS:
            if (case['orbit_id'], a) in by and (case['orbit_id'], b) in by:
                row = compare(by[(case['orbit_id'], a)], by[(case['orbit_id'], b)], case)
                row.update(orbit_id=case['orbit_id'], pair=f'{a}-{b}', radial=case.get('radial'))
                pairs.append(row)
    q2 = [p for p in pairs if not p['Q2']]
    return dict(years=years, jobs=len(level_rows), Q1_failures=[(r['orbit_id'], r['variant'], r['error']) for r in q1],
                Q2_violations=q2, passed=not q1 and not q2, pairs=pairs)


def report(summary, level_rows):
    years = summary['years']
    print(f"\n=== {years:g} лет: заданий {summary['jobs']}; P2-Q1 нарушений {len(summary['Q1_failures'])}; "
          f"P2-Q2 нарушений {len(summary['Q2_violations'])} -> {'ПРОЙДЕНО' if summary['passed'] else 'НЕ ПРОЙДЕНО'}")
    for name in ('D-T', 'D-B', 'T-B'):
        sub = [p for p in summary['pairs'] if p['pair'] == name]
        if not sub:
            continue
        diverged = [p for p in sub if p['tau_seconds'] is not None]
        post = [p for p in diverged if not p['same_outcome']]
        taus = sorted(p['tau_years'] for p in diverged)
        print(f"  {name}: расхождение >1e-6 в {len(diverged)}/{len(sub)} случаях"
              + (f" (τ: мин {taus[0]:.3g}, медиана {taus[len(taus)//2]:.3g} лет)" if taus else '')
              + f"; событий проверено до τ {sum(p['event_checked_before_tau'] for p in sub)}; "
              f"исходы разошлись после τ: {len(post)}")
    for v in ('D', 'T', 'B'):
        rows = [r for r in level_rows if r['variant'] == v]
        if rows:
            ecc = [r['eccentricity'] for r in rows if r.get('eccentricity')]
            high = sum(1 for e in ecc if e['first_bound_at_limit_time'] is not None)
            print(f"  {v}: сумма заданий {sum(r['wall'] for r in rows):.0f} с, шагов {sum(r['steps'] for r in rows)}"
                  + (f"; связанный e >= 0.9 в {high}/{len(ecc)}; max e связанных {max(e['max_bound'] for e in ecc):.3f}" if ecc else ''))
    for r in summary['Q1_failures']:
        print(f"  P2-Q1: {r}")
    for p in summary['Q2_violations']:
        print(f"  P2-Q2: {p['orbit_id']} {p['pair']}: исходы {p['outcomes']}, |Δt| {p['dt_periods']}, τ {p['tau_periods']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--years', type=float, nargs='*', default=[1.0, 10.0, 100.0, 1000.0])
    parser.add_argument('--b-max-years', type=float, default=10.0)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--run-dir', default='scratch/claude/W2-P004_P2-1')
    args = parser.parse_args()

    run_dir = ROOT/args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = run_dir/'jobs.jsonl'
    done = {}
    if jobs_file.exists():
        for line in jobs_file.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if row.get('prototype_version') == PROTOTYPE_VERSION:
                done[(row['orbit_id'], row['years'], row['variant'])] = row
    _, cases = load_cases(ROOT)
    cases = cases[:args.limit]
    info = cpu_info()
    print(f"P2-1: прототип v{PROTOTYPE_VERSION}; случаев {len(cases)}; ступени {args.years} лет; "
          f"B до {args.b_max_years:g} лет; процессов {args.workers}; уже готово заданий {len(done)}; "
          f"каталог {run_dir.relative_to(ROOT)}", flush=True)
    summaries = []
    started = time.perf_counter()
    for years in args.years:
        variants = ['T', 'D'] + (['B'] if years <= args.b_max_years else [])
        jobs = [dict(case=c, years=years, variant=v) for v in (['B'] + variants[:2] if 'B' in variants else variants)
                for c in sorted(cases, key=lambda c: c['period_seconds'])
                if (c['orbit_id'], years, v) not in done]
        if jobs:
            print(f'\n{years:g} лет: заданий к выполнению {len(jobs)}', flush=True)
            with mp.get_context('spawn').Pool(args.workers, initializer=_init) as pool, \
                    jobs_file.open('a', encoding='utf-8') as stream:
                for k, res in enumerate(pool.imap_unordered(_job, jobs, chunksize=1), 1):
                    stream.write(json.dumps(res, ensure_ascii=False, default=float)+'\n')
                    stream.flush()
                    done[(res['orbit_id'], res['years'], res['variant'])] = res
                    if k % 12 == 0 or k == len(jobs):
                        print(f'  {k}/{len(jobs)} заданий, {time.perf_counter()-started:.0f} с с начала', flush=True)
        level_rows = [done[(c['orbit_id'], years, v)] for c in cases for v in variants]
        summary = evaluate(level_rows, cases, years)
        report(summary, level_rows)
        summaries.append({k: v for k, v in summary.items()})
        (run_dir/f'level_{years:g}y.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1,
                                                                 default=float), encoding='utf-8')
        if not summary['passed']:
            print(f'\nСтупень {years:g} лет не пройдена — следующие ступени не запускаются (план §3.1).')
            break
    verdict = all(s['passed'] for s in summaries) and len(summaries) == len(args.years)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (run_dir/'summary.json').write_text(json.dumps(dict(plan='W2-P004', stage='P2-1', utc=stamp,
        prototype_version=PROTOTYPE_VERSION, machine=info, years=args.years, b_max_years=args.b_max_years,
        cases=[c['orbit_id'] for c in cases], levels=[dict(years=s['years'], passed=s['passed'],
        Q1_failures=s['Q1_failures'], Q2_violations=s['Q2_violations']) for s in summaries],
        verdict_passed=verdict, wall_seconds=time.perf_counter()-started), ensure_ascii=False, indent=1,
        default=float), encoding='utf-8')
    print(f"\nИТОГ P2-1: {'ПРОЙДЕНО' if verdict else 'НЕ ПРОЙДЕНО / НЕ ЗАВЕРШЕНО'}; каталог {run_dir.relative_to(ROOT)}")
    return 0 if verdict else 1


if __name__ == '__main__':
    raise SystemExit(main())
