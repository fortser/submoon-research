"""W2-P004, ступень P2-1: точность режима на ступенях горизонта 1–1000 лет.

Критерии зафиксированы до расчёта: docs/tasks/W2_compiled_mode_admission_v1.md §3.1.
Варианты: D — прототип NumericMode(); T — прототип NumericMode.tight();
B — IAS15/REBOUNDx (только до --b-max-years). Пары D–T, D–B, T–B.
P2-Q1: все прогоны completed. P2-Q2: если событие наступает до времени расхождения
τ (|Δr|/a или |Δv|/(na) > 1e-6) или τ нет — тот же исход/тип/тело и |Δt| <= 1e-6 P0.
Остальное — отчёт. Ступени идут по порядку; следующая — только после пройденной.

Каждое завершённое задание сразу дописывается в jobs.jsonl каталога запуска;
повторный запуск продолжает с места остановки. Версия 2 (W2-I011, W2-I012): строка
принимается к продолжению только при совпадении отпечатка задания — старт, физика,
режим, горизонт, вариант, сетка и SHA фактической реализации (исходники, константы,
версии библиотек); итог «пройдено» — только для полного ожидаемого набора.

Версия 4 (план §3.2, уточнение 2026-10-09 до удалённого запуска): обязательный набор
§3.1 (--core-years, B до --core-b-max) оценивается отдельно от расширения (B дольше
--core-b-max, ступени вне --core-years) и выполняется первым; расширение не влияет на итог
P2-1 и засчитывается, только если пройдены все предыдущие ступени. --deadline-utc и
--stop-file останавливают задания (run_status=partial, «остановлено сроком»): в расширении это
неполнота, а не численный отказ; в обязательном наборе — нарушение P2-Q1. Пары с остановленным
вариантом сравниваются только на общем отрезке времени. --evaluate-only — оценка готового
jobs.jsonl без расчёта.

    ~/venvs/submoon-w2/bin/python scripts/admission_p2_horizons.py --workers 6
"""
from __future__ import annotations

import argparse
import hashlib
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
from submoon_research.dynamics.compiled_dop853 import (  # noqa: E402
    PROTOTYPE_VERSION, implementation_fingerprint, integrate_compiled)
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases, warmup  # noqa: E402

YEAR = 365.25*86400.0
LIMIT = 1e-6
PAIRS = (('D', 'T'), ('D', 'B'), ('T', 'B'))
FULL_YEARS = (1.0, 10.0, 100.0, 1000.0)
FULL_B_MAX = 10.0
FULL_CASE_COUNT = 48
STAND_VERSION = 4
STOPPED = 'stopped_by_deadline'
_B_MODULES = ('dynamics/engine_compare.py', 'dynamics/native_ias15.py', 'dynamics/dense_segments.py',
              'dynamics/fast_forces.py', 'events/dense_contact.py', 'events/escape.py')


def b_fingerprint():
    import importlib.metadata as metadata
    digest = hashlib.sha256()
    for name in _B_MODULES:
        digest.update(name.encode())
        digest.update((ROOT/'src/submoon_research'/name).read_bytes())
    for package in ('rebound', 'reboundx', 'numpy', 'scipy', 'numba'):
        digest.update(f'{package}={metadata.version(package)}'.encode())
    return digest.hexdigest()


def job_fingerprint(case, years, variant, implementations):
    mode = NumericMode.tight() if variant == 'T' else NumericMode()
    payload = dict(stand=STAND_VERSION, orbit_id=case['orbit_id'], state=case['state'],
                   gms=case['gms'], figures=case['figures'], radii=list(map(float, case['radii'])),
                   hill_km=case.get('hill_km'), escape_window=case.get('escape_window'),
                   period=case['period_seconds'], a_km=case['a_km'], years=years, variant=variant,
                   mode=mode.__dict__, grid='0+linspace65+geomspace128(1e-4H..H)',
                   implementation=implementations['B' if variant == 'B' else 'compiled'])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=float).encode()).hexdigest()


def grid(horizon):
    return np.unique(np.concatenate([[0.0], np.linspace(0, horizon, 65),
                                     np.geomspace(1e-4*horizon, horizon, 128)]))


def _init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


class Budget:
    """Остановка задания по сроку (epoch, UTC) или по стоп-файлу (проверка раз в 5 с).
    TimeoutError движки превращают в run_status='partial' с last_valid_time."""
    def __init__(self, deadline=None, stop_file=None):
        self.deadline, self.stop_file, self.next_check = deadline, stop_file, 0.0

    def __call__(self):
        now = time.time()
        if self.deadline is not None and now >= self.deadline:
            raise TimeoutError(STOPPED+': срок стенда')
        if self.stop_file and now >= self.next_check:
            self.next_check = now+5.0
            if os.path.exists(self.stop_file):
                raise TimeoutError(STOPPED+': стоп-файл')


def _job(job):
    case, years, variant = job['case'], job['years'], job['variant']
    horizon = years*YEAR
    mode = NumericMode.tight() if variant == 'T' else NumericMode()
    kwargs = dict(period=case['period_seconds'], mode=mode, hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=grid(horizon),
                  budget=Budget(job.get('deadline'), job.get('stop_file')))
    started = time.perf_counter()
    if variant == 'B':
        out = integrate_engine('ias15_reboundx', case['state'], case['gms'], case['figures'],
                               case['radii'], horizon, **kwargs)
    else:
        out = integrate_compiled(case['state'], case['gms'], case['figures'], case['radii'],
                                 horizon, **kwargs)
    event = out['event']
    return dict(orbit_id=case['orbit_id'], years=years, variant=variant,
        prototype_version=PROTOTYPE_VERSION, job_fingerprint=job['fingerprint'],
        wall=time.perf_counter()-started,
        run_status=out['run_status'], error=out['error'], outcome=out['physical_outcome'],
        last_valid_time=out['last_valid_time'],
        event=None if event is None else dict(event=event['event'], time=event['time'],
                                              body=event.get('body_index')),
        steps=out['steps'], nfev=out['nfev'], eccentricity=out.get('eccentricity_monitor'),
        numerical_domain=out.get('numerical_domain'), endpoint_residual=out.get('endpoint_residual'),
        stopped=out['run_status'] == 'partial' and STOPPED in str(out['error']),
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
    truncated = x['run_status'] != 'completed' or y['run_status'] != 'completed'
    common = min(x['last_valid_time'], y['last_valid_time']) if truncated else math.inf
    # С незавершённым вариантом сравнивается только общий отрезок [0, common]: события позже
    # него не проверяемы, исходы (unresolved) не сравниваются.
    ex = x['event'] if x['event'] and x['event']['time'] <= common else None
    ey = y['event'] if y['event'] and y['event']['time'] <= common else None
    times = [e['time'] for e in (ex, ey) if e]
    first_event = min(times) if times else None
    checked = first_event is not None and (tau is None or first_event < tau)
    same = ((truncated or x['outcome'] == y['outcome']) and (ex is None) == (ey is None)
            and (ex is None or (ex['event'] == ey['event'] and ex['body'] == ey['body'])))
    dt = abs(ex['time']-ey['time'])/period if ex and ey else None
    q2 = True
    if checked:
        q2 = same and dt is not None and dt <= LIMIT
    elif first_event is None and tau is None:
        q2 = same
    return dict(tau_seconds=tau, tau_periods=None if tau is None else tau/period,
                tau_years=None if tau is None else tau/YEAR, max_dr_before_tau=dr_max,
                max_dv_before_tau=dv_max, event_checked_before_tau=checked, same_outcome=same,
                dt_periods=dt, Q2=q2, outcomes=(x['outcome'], y['outcome']), truncated=truncated,
                common_time=None if not truncated else common)


def evaluate(level_rows, cases, years, variants=None, full_scope=True, allow_stopped=False, focus=None):
    """Итог ступени. «Пройдено» — только при полном наборе случаев/вариантов без повторов,
    полном объёме и выполнении P2-Q1/Q2; иначе — не пройдено с причинами (W2-I012).
    allow_stopped (только расширение): задания, остановленные сроком, — неполнота
    (complete=False), а не нарушение P2-Q1. focus — варианты, по которым считаются P2-Q1 и
    пары (пара входит, если хотя бы один её вариант в focus); полнота — по всем variants."""
    variants = variants or (['D', 'T', 'B'] if years <= FULL_B_MAX else ['D', 'T'])
    focus = set(focus or variants)
    problems = []
    if not cases or not level_rows:
        problems.append('пустой набор')
    keys = [(r['orbit_id'], r['variant']) for r in level_rows]
    expected = {(c['orbit_id'], v) for c in cases for v in variants}
    if len(keys) != len(set(keys)):
        problems.append('повторяющиеся строки')
    if set(keys) != expected:
        problems.append(f'неполный набор: отсутствует {len(expected - set(keys))}, лишних {len(set(keys) - expected)}')
    if not full_scope:
        problems.append('сокращённый разработочный объём — не допуск')
    by = {(r['orbit_id'], r['variant']): r for r in level_rows}
    own = [r for r in level_rows if r['variant'] in focus]
    stopped = [r for r in own if allow_stopped and r.get('stopped')]
    q1 = [r for r in own if r['run_status'] != 'completed' and not (allow_stopped and r.get('stopped'))]
    pairs = []
    for case in cases:
        for a, b in PAIRS:
            if (a in focus or b in focus) and (case['orbit_id'], a) in by and (case['orbit_id'], b) in by:
                row = compare(by[(case['orbit_id'], a)], by[(case['orbit_id'], b)], case)
                row.update(orbit_id=case['orbit_id'], pair=f'{a}-{b}', radial=case.get('radial'))
                pairs.append(row)
    q2 = [p for p in pairs if not p['Q2']]
    return dict(years=years, variants=list(variants), focus=sorted(focus), jobs=len(level_rows),
                Q1_failures=[(r['orbit_id'], r['variant'], r['error']) for r in q1],
                Q2_violations=q2, problems=problems, passed=not q1 and not q2 and not problems,
                stopped=[dict(orbit_id=r['orbit_id'], variant=r['variant'],
                              reached_years=r['last_valid_time']/YEAR) for r in stopped],
                complete=not stopped, pairs=pairs, stand_version=STAND_VERSION)


def blocking(summary):
    """Проблемы, кроме пометки сокращённого объёма, — останавливают зачёт следующих ступеней.
    Остановка сроком в расширении (stopped) — неполнота, зачёт не останавливает."""
    return bool(summary['Q1_failures'] or summary['Q2_violations']
                or [p for p in summary['problems'] if not p.startswith('сокращённый')])


def report(summary, level_rows, label=''):
    years = summary['years']
    state = 'ПРОЙДЕНО' if summary['passed'] else 'НЕ ПРОЙДЕНО'
    if summary['passed'] and not summary.get('complete', True):
        state = f"нарушений нет, НЕ ЗАВЕРШЕНО (остановлено сроком: {len(summary['stopped'])})"
    focus = set(summary.get('focus') or ('D', 'T', 'B'))
    print(f"\n=== {years:g} лет{label}: варианты {'/'.join(sorted(focus))}; заданий {summary['jobs']}; "
          f"P2-Q1 нарушений {len(summary['Q1_failures'])}; P2-Q2 нарушений {len(summary['Q2_violations'])} -> {state}")
    for name in ('D-T', 'D-B', 'T-B'):
        sub = [p for p in summary['pairs'] if p['pair'] == name]
        if not sub:
            continue
        diverged = [p for p in sub if p['tau_seconds'] is not None]
        post = [p for p in diverged if not p['same_outcome']]
        taus = sorted(p['tau_years'] for p in diverged)
        truncated = sum(1 for p in sub if p.get('truncated'))
        print(f"  {name}: расхождение >1e-6 в {len(diverged)}/{len(sub)} случаях"
              + (f" (τ: мин {taus[0]:.3g}, медиана {taus[len(taus)//2]:.3g} лет)" if taus else '')
              + f"; событий проверено до τ {sum(p['event_checked_before_tau'] for p in sub)}; "
              f"исходы разошлись после τ: {len(post)}"
              + (f"; сравнено на общем отрезке (вариант остановлен): {truncated}" if truncated else ''))
    for v in ('D', 'T', 'B'):
        rows = [r for r in level_rows if r['variant'] == v and v in focus]
        if rows:
            ecc = [r['eccentricity'] for r in rows if r.get('eccentricity')]
            high = sum(1 for e in ecc if e['first_bound_at_limit_time'] is not None)
            outside = sum(1 for r in rows if (r.get('numerical_domain') or {}).get('outside_validated_domain'))
            print(f"  {v}: сумма заданий {sum(r['wall'] for r in rows):.0f} с, шагов {sum(r['steps'] for r in rows)}"
                  + (f"; флаг «вне проверенной области» (e >= 0.9) у {outside}" if v != 'B' else '')
                  + (f"; связанный e >= 0.9 в {high}/{len(ecc)}; max e связанных {max(e['max_bound'] for e in ecc):.3f}" if ecc else ''))
            stopped = sorted(r['last_valid_time']/YEAR for r in rows if r.get('stopped'))
            if stopped:
                print(f"  {v}: остановлено сроком {len(stopped)}; достигнуто лет: мин {stopped[0]:.4g}, "
                      f"медиана {stopped[len(stopped)//2]:.4g}, макс {stopped[-1]:.4g}")
    for problem in summary.get('problems', []):
        print(f"  ПРОБЛЕМА: {problem}")
    for r in summary['Q1_failures']:
        print(f"  P2-Q1: {r}")
    for p in summary['Q2_violations']:
        print(f"  P2-Q2: {p['orbit_id']} {p['pair']}: исходы {p['outcomes']}, |Δt| {p['dt_periods']}, τ {p['tau_periods']}")


COST_FACTOR = dict(D=1.0, T=2.2, B=30.0)  # относительная цена года по замерам W2-R008/R009


def estimated_cost(job):
    return job['years']*YEAR/job['case']['period_seconds']*COST_FACTOR[job['variant']]


def level_plan(years_list, b_max, core_years, core_b_max):
    """Варианты ступени и их принадлежность обязательному набору (core) или расширению."""
    plan = {}
    for years in years_list:
        variants = ['T', 'D'] + (['B'] if years <= b_max else [])
        core = [v for v in variants if years in core_years and (v != 'B' or years <= core_b_max)]
        plan[years] = dict(variants=variants, core=core, extension=[v for v in variants if v not in core])
    return plan


def order_jobs(jobs):
    """Сначала обязательный набор, затем расширение; внутри группы — самые долгие первыми."""
    return sorted(jobs, key=lambda job: (not job['core'], -estimated_cost(job)))


def parse_deadline(text):
    if not text:
        return None
    moment = datetime.fromisoformat(text.strip().replace('Z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def read_rows(jobs_file, expected_fp):
    """Строки jobs.jsonl с совпавшим отпечатком; для ключа берётся последняя строка.
    Повреждённая (недописанная) строка пропускается и учитывается как отброшенная."""
    rows, ignored = {}, 0
    if not jobs_file.exists():
        return rows, ignored
    for line in jobs_file.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            key = (row['orbit_id'], row['years'], row['variant'])
        except (ValueError, KeyError, TypeError):
            ignored += 1
            continue
        if expected_fp.get(key) is not None and row.get('job_fingerprint') == expected_fp[key]:
            rows[key] = row
        else:
            ignored += 1
    return rows, ignored


def run_jobs(jobs, workers, jobs_file, done, run_dir, planned, started):
    """Выполняет задания пулом spawn; каждый результат сразу дописывается в jobs.jsonl,
    progress.json обновляется после каждого задания (для наблюдения извне)."""
    if not jobs:
        return
    progress = run_dir/'progress.json'

    def write_progress(last=None):
        by_level = {f'{y:g}': dict(done=sum(1 for k in keys if k in done), planned=len(keys),
                                   stopped=sum(1 for k in keys if k in done and done[k].get('stopped')))
                    for y, keys in planned.items()}
        tmp = progress.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(updated_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=time.perf_counter()-started, done=len(done),
            planned=sum(len(k) for k in planned.values()), by_level=by_level, last=last),
            ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(progress)

    write_progress()
    with mp.get_context('spawn').Pool(workers, initializer=_init) as pool, \
            jobs_file.open('a', encoding='utf-8') as stream:
        for k, res in enumerate(pool.imap_unordered(_job, jobs, chunksize=1), 1):
            stream.write(json.dumps(res, ensure_ascii=False, default=float)+'\n')
            stream.flush()
            done[(res['orbit_id'], res['years'], res['variant'])] = res
            write_progress(dict(orbit_id=res['orbit_id'], years=res['years'], variant=res['variant'],
                                status=res['run_status'], wall=res['wall']))
            if k % 12 == 0 or k == len(jobs):
                print(f'  {k}/{len(jobs)} заданий, {time.perf_counter()-started:.0f} с с начала', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--years', type=float, nargs='*', default=list(FULL_YEARS))
    parser.add_argument('--b-max-years', type=float, default=FULL_B_MAX)
    parser.add_argument('--core-years', type=float, nargs='*', default=list(FULL_YEARS),
                        help='ступени обязательного набора (§3.1); прочие ступени — расширение')
    parser.add_argument('--core-b-max', type=float, default=FULL_B_MAX,
                        help='B в обязательном наборе до этой ступени; дольше — расширение')
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--run-dir', default='scratch/claude/W2-P004_P2-1')
    parser.add_argument('--concurrent', action='store_true',
                        help='все ступени сразу (обязательный набор первым, внутри — самые долгие); '
                             'оценка — по-прежнему последовательная')
    parser.add_argument('--deadline-utc', default=None,
                        help='ISO 8601 UTC: к этому сроку незавершённые задания останавливаются (partial)')
    parser.add_argument('--stop-file', default=None,
                        help='если файл появится, задания останавливаются (partial) и стенд подводит итог')
    parser.add_argument('--evaluate-only', action='store_true',
                        help='только оценка имеющегося jobs.jsonl (остановленные строки учитываются)')
    args = parser.parse_args()

    run_dir = ROOT/args.run_dir
    run_dir.mkdir(parents=True, exist_ok=True)
    jobs_file = run_dir/'jobs.jsonl'
    deadline = parse_deadline(args.deadline_utc)
    _, cases = load_cases(ROOT)
    cases = cases[:args.limit]
    # Полный объём — 48 случаев и обязательные ступени 1–1000 лет с B до 10 лет; расширение
    # (10 000 лет, B до 1000 лет) идёт сверх обязательного набора и оценивается отдельно (§3.2).
    full_scope = (args.limit is None and len(cases) == FULL_CASE_COUNT
                  and tuple(args.core_years) == FULL_YEARS and all(y in args.years for y in FULL_YEARS)
                  and args.core_b_max >= FULL_B_MAX and args.b_max_years >= FULL_B_MAX)
    implementations = dict(compiled=implementation_fingerprint(), B=b_fingerprint())
    expected_fp = {(c['orbit_id'], y, v): job_fingerprint(c, y, v, implementations)
                   for c in cases for y in args.years for v in ('D', 'T', 'B')}
    rows, ignored = read_rows(jobs_file, expected_fp)
    # Остановленные сроком строки при продолжении пересчитываются; при --evaluate-only учитываются.
    done = {k: r for k, r in rows.items() if args.evaluate_only or r['run_status'] != 'partial'}
    info = cpu_info()
    plan = level_plan(args.years, args.b_max_years, args.core_years, args.core_b_max)
    planned = {y: [(c['orbit_id'], y, v) for c in cases for v in plan[y]['variants']] for y in args.years}
    mode_text = ('только оценка' if args.evaluate_only
                 else 'одновременный' if args.concurrent else 'по ступеням')
    print(f"P2-1: стенд v{STAND_VERSION}; прототип v{PROTOTYPE_VERSION}; случаев {len(cases)}; ступени {args.years} лет "
          f"(обязательные {args.core_years}, B до {args.b_max_years:g} лет, в обязательном — до {args.core_b_max:g}); "
          f"процессов {args.workers}; режим {mode_text}; уже готово заданий {len(done)} "
          f"(отброшено несовпавших по отпечатку {ignored}); срок {args.deadline_utc or 'нет'}; "
          f"объём {'полный' if full_scope else 'СОКРАЩЁННЫЙ'}; каталог {run_dir.relative_to(ROOT)}", flush=True)
    if args.stop_file and os.path.exists(args.stop_file) and not args.evaluate_only:
        print(f'ВНИМАНИЕ: стоп-файл {args.stop_file} уже существует — задания будут сразу остановлены', flush=True)
    by_id = {c['orbit_id']: c for c in cases}

    def jobs_for(levels):
        return [dict(case=by_id[o], years=y, variant=v, fingerprint=expected_fp[(o, y, v)],
                     core=v in plan[y]['core'], deadline=deadline, stop_file=args.stop_file)
                for lvl in levels for (o, y, v) in planned[lvl] if (o, y, v) not in done]

    summaries = []
    started = time.perf_counter()
    if args.concurrent and not args.evaluate_only:
        jobs = order_jobs(jobs_for(args.years))
        print(f'\nвсе ступени: заданий к выполнению {len(jobs)} '
              f'(обязательных {sum(j["core"] for j in jobs)}, расширения {sum(not j["core"] for j in jobs)})', flush=True)
        run_jobs(jobs, args.workers, jobs_file, done, run_dir, planned, started)
    core_stop = ext_stop = None
    for years in args.years:
        if not args.concurrent and not args.evaluate_only and core_stop is None:
            jobs = order_jobs(jobs_for([years]))
            if jobs:
                print(f'\n{years:g} лет: заданий к выполнению {len(jobs)}', flush=True)
            run_jobs(jobs, args.workers, jobs_file, done, run_dir, planned, started)
        level = plan[years]
        level_rows = [done[k] for k in planned[years] if k in done]
        record = dict(years=years, stand_version=STAND_VERSION, core=None, extension=None)
        if level['core']:
            rows_core = [r for r in level_rows if r['variant'] in level['core']]
            if rows_core or core_stop is None:
                summary = evaluate(rows_core, cases, years, level['core'], full_scope)
                summary.update(kind='core', counted=core_stop is None)
                report(summary, rows_core, ' (обязательный набор)')
                if not summary['counted']:
                    print(f'  (не засчитывается: не пройдена обязательная ступень {core_stop:g} лет)')
                summaries.append(summary)
                record['core'] = summary
                if core_stop is None and blocking(summary):
                    core_stop = years
                    print(f'\nОбязательная ступень {years:g} лет не пройдена — следующие ступени не засчитываются (§3.1).')
        if level['extension']:
            if level_rows or (core_stop is None and ext_stop is None):
                summary = evaluate(level_rows, cases, years, level['variants'], full_scope,
                                   allow_stopped=True, focus=level['extension'])
                summary.update(kind='extension', counted=core_stop is None and ext_stop is None)
                report(summary, level_rows, ' (расширение)')
                if not summary['counted']:
                    print(f"  (расширение не засчитывается: не пройдена ступень "
                          f"{core_stop if core_stop is not None else ext_stop:g} лет)")
                summaries.append(summary)
                record['extension'] = summary
                if core_stop is None and ext_stop is None and blocking(summary):
                    ext_stop = years
        (run_dir/f'level_{years:g}y.json').write_text(json.dumps(record, ensure_ascii=False, indent=1,
                                                                 default=float), encoding='utf-8')
    core = [s for s in summaries if s['kind'] == 'core']
    extension = [s for s in summaries if s['kind'] == 'extension']
    core_levels = {s['years'] for s in core if s['counted'] and s['passed']}
    verdict = (full_scope and core_stop is None and all(y in core_levels for y in FULL_YEARS)
               and all(s['passed'] and s['counted'] for s in core))
    extension_passed = bool(extension) and all(s['passed'] and s['counted'] and s['complete'] for s in extension)

    def brief(s):
        return dict(years=s['years'], kind=s['kind'], variants=s['variants'], focus=s['focus'],
                    passed=s['passed'], complete=s['complete'], counted=s['counted'],
                    stopped=len(s['stopped']), Q1_failures=s['Q1_failures'],
                    Q2_violations=s['Q2_violations'], problems=s['problems'])

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    (run_dir/'summary.json').write_text(json.dumps(dict(plan='W2-P004', stage='P2-1', utc=stamp,
        prototype_version=PROTOTYPE_VERSION, machine=info, years=args.years, b_max_years=args.b_max_years,
        core_years=args.core_years, core_b_max=args.core_b_max, concurrent=args.concurrent,
        evaluate_only=args.evaluate_only, deadline_utc=args.deadline_utc, stop_file=args.stop_file,
        workers=args.workers, cases=[c['orbit_id'] for c in cases], levels=[brief(s) for s in core],
        extension_levels=[brief(s) for s in extension], verdict_passed=verdict,
        extension_passed=extension_passed, full_scope=full_scope, stand_version=STAND_VERSION,
        implementations=implementations, wall_seconds=time.perf_counter()-started),
        ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    clean = core_stop is None and ext_stop is None and all(s['counted'] for s in summaries)
    if verdict:
        print(f"\nИТОГ P2-1 (обязательный набор): ПРОЙДЕНО; каталог {run_dir.relative_to(ROOT)}")
    elif clean and not full_scope:
        print(f"\nИТОГ: СОКРАЩЁННЫЙ ОБЪЁМ — нарушений не найдено, но это не допуск P2-1; каталог {run_dir.relative_to(ROOT)}")
    else:
        print(f"\nИТОГ P2-1 (обязательный набор): НЕ ПРОЙДЕНО / НЕ ЗАВЕРШЕНО; каталог {run_dir.relative_to(ROOT)}")
    if extension:
        stopped = sum(len(s['stopped']) for s in extension)
        print(f"РАСШИРЕНИЕ: {'пройдено полностью' if extension_passed else 'не пройдено или не завершено'}; "
              f"ступеней {len(extension)}, остановлено сроком заданий {stopped}")
    return 0 if verdict or (clean and not full_scope) else 1


if __name__ == '__main__':
    raise SystemExit(main())
