"""W2-T008: замер движка A (dop853_jit) против исправленного C (hierarchical_cached).

Три части:
1. Чистый замер: 12 performance_cases из W2_three_engines_v1, одинаковый горизонт
   в периодах, повторы с чередованием порядка движков, один процесс, без
   инструментирования. Кеш C готовится заранее; его подготовка печатается
   отдельно и учитывается в амортизированной оценке.
2. Разбивка времени (один инструментированный прогон на случай): исключающее
   время по категориям — силы (Numba), кеш массивных тел, события (контакт,
   уход), вычисление dense-полиномов, интегратор DOP853 (шаг, dense output,
   перезапуски; включает Python-обвязку RHS), диагностики энергии и прочее.
3. По ключу --scaling: пропускная способность при 1..N процессах (spawn),
   как в боевых кампаниях: у C каждый процесс строит свой кеш.

Сходство исходов A и C печатается как диагностика, не как численный допуск.
Это машинный замер, не научный результат.

Запуск из корня проекта (WSL):
    ~/venvs/submoon-w2/bin/python scripts/bench_engines_ac.py --periods 20 --repeats 3
    ~/venvs/submoon-w2/bin/python scripts/bench_engines_ac.py --scaling
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

import multiprocessing as mp  # noqa: E402

import numpy as np  # noqa: E402
import psutil  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from scipy.integrate import DOP853  # noqa: E402
from scipy.integrate._ivp.base import DenseOutput  # noqa: E402

from submoon_research.dynamics import engine_compare, fast_forces  # noqa: E402
from submoon_research.dynamics.dense_segments import CombinedSegment, PowerSegment  # noqa: E402
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.dynamics.native_ias15 import MassiveCache  # noqa: E402
from submoon_research.events.escape import EscapeTracker  # noqa: E402
from submoon_research.workflows.w2_comparison import (  # noqa: E402
    load_cases, performance_cases, run_case, warmup)

ENGINES = ('dop853_jit', 'hierarchical_cached')
SHORT = {'dop853_jit': 'A', 'hierarchical_cached': 'C'}
CATEGORIES = ('forces', 'massive_cache', 'events', 'dense_eval', 'integrator_step',
              'integrator_dense_output', 'integrator_restart', 'diagnostics', 'other')


def cpu_info():
    info = dict(platform=platform.platform(), python=platform.python_version(),
                executable=sys.executable, node=platform.node(),
                logical_cpus=os.cpu_count(), physical_cores=psutil.cpu_count(logical=False),
                memory_total_gib=round(psutil.virtual_memory().total/2**30, 2))
    try:
        out = subprocess.run(['lscpu', '-J'], capture_output=True, text=True, timeout=10)
        fields = {}

        def walk(items):
            for item in items:
                key = item.get('field', '').rstrip(':').strip()
                if key and key != 'Flags':
                    fields[key] = item.get('data')
                walk(item.get('children', []))
        walk(json.loads(out.stdout)['lscpu'])
        info['lscpu'] = fields
    except Exception as exc:  # lscpu отсутствует (Windows) — не критично
        info['lscpu_error'] = repr(exc)
    return info


class Profiler:
    """Исключающее время по категориям: время вложенных обёрток вычитается."""

    def __init__(self):
        self.exclusive = defaultdict(float)
        self.calls = defaultdict(int)
        self.stack = []
        self.patches = []

    def wrap(self, category, fn):
        prof = self

        def wrapper(*args, **kwargs):
            started = time.perf_counter()
            prof.stack.append(0.0)
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed = time.perf_counter()-started
                child = prof.stack.pop()
                prof.exclusive[category] += elapsed-child
                prof.calls[category] += 1
                if prof.stack:
                    prof.stack[-1] += elapsed
        return wrapper

    def patch(self, owner, name, category):
        original = getattr(owner, name)
        self.patches.append((owner, name, original))
        setattr(owner, name, self.wrap(category, original))

    def __enter__(self):
        for name in ('rhs', 'probe_acceleration'):
            self.patch(fast_forces, name, 'forces')
        self.patch(fast_forces, 'energy', 'diagnostics')
        self.patch(MassiveCache, '__call__', 'massive_cache')
        self.patch(MassiveCache, 'segment_at', 'massive_cache')
        self.patch(PowerSegment, '__call__', 'massive_cache')
        self.patch(engine_compare, 'polynomial_contact', 'events')
        if hasattr(engine_compare, 'chebyshev_samples'):  # общая подготовка событий шага (W2-T008)
            self.patch(engine_compare, 'chebyshev_samples', 'events')
        self.patch(EscapeTracker, 'advance', 'events')
        self.patch(DenseOutput, '__call__', 'dense_eval')
        self.patch(CombinedSegment, '__call__', 'dense_eval')
        self.patch(DOP853, 'step', 'integrator_step')
        self.patch(DOP853, 'dense_output', 'integrator_dense_output')
        self.patch(DOP853, '__init__', 'integrator_restart')
        return self

    def __exit__(self, *exc):
        for owner, name, original in reversed(self.patches):
            setattr(owner, name, original)
        self.patches.clear()


def run(engine, case, horizon, mode, cache):
    started = time.perf_counter()
    out = integrate_engine(engine, case['state'], case['gms'], case['figures'], case['radii'],
        horizon, period=case['period_seconds'], mode=mode,
        hill_km=case.get('hill_km'), escape_window=case.get('escape_window'),
        times=np.linspace(0, horizon, 65), cache=cache if engine == 'hierarchical_cached' else None)
    out['wall_total'] = time.perf_counter()-started
    return out


def agreement(a, c, case):
    fa, fc = np.asarray(a['final_state']).reshape(-1, 6), np.asarray(c['final_state']).reshape(-1, 6)
    ta, tc = a['last_valid_time'], c['last_valid_time']
    same_time = math.isclose(ta, tc, rel_tol=0, abs_tol=1e-9*case['period_seconds'])
    rel = None
    if same_time:
        rel = float(np.linalg.norm((fa[-1, :3]-fa[0, :3])-(fc[-1, :3]-fc[0, :3]))/case['a_km'])
    return dict(same_outcome=a['physical_outcome'] == c['physical_outcome'],
                outcome_A=a['physical_outcome'], outcome_C=c['physical_outcome'],
                end_time_difference_periods=(tc-ta)/case['period_seconds'],
                probe_position_difference_over_a=rel)


def prepare_caches(cases, horizons, mode):
    need = {}
    for case in cases:
        host = case['host']
        if host not in need or need[host][1] < horizons[case['orbit_id']]:
            need[host] = (case, max(horizons[case['orbit_id']], need.get(host, (None, 0.0))[1]))
    caches = {}
    for host, (case, horizon) in need.items():
        cache = MassiveCache(case['state'], case['gms'], case['figures'], epsilon=mode.cache_epsilon)
        cache.extend(horizon, lambda: None)
        caches[host] = cache
        print(f'кеш {host}: {len(cache.segments)} сегментов, {cache.bytes/2**20:.1f} МиБ, '
              f'подготовка {cache.wall_seconds:.2f} с', flush=True)
    return caches


def single_process(cases, args, mode):
    horizons = {c['orbit_id']: c['period_seconds']*args.periods for c in cases}
    caches = prepare_caches(cases, horizons, mode)
    for engine in ENGINES:  # прогрев JIT и путей кода на коротком горизонте
        case = cases[0]
        run(engine, case, case['period_seconds'], mode, caches[case['host']])

    clean = defaultdict(lambda: defaultdict(list))
    results = {}
    for repeat in range(args.repeats):
        order = ENGINES if repeat % 2 == 0 else ENGINES[::-1]
        for case in cases:
            for engine in order:
                out = run(engine, case, horizons[case['orbit_id']], mode, caches[case['host']])
                clean[case['orbit_id']][engine].append(out['wall_total'])
                results[(case['orbit_id'], engine)] = out
        print(f'чистый замер: повтор {repeat+1}/{args.repeats} готов', flush=True)

    breakdown = {}
    for case in cases:
        for engine in ENGINES:
            with Profiler() as prof:
                out = run(engine, case, horizons[case['orbit_id']], mode, caches[case['host']])
            accounted = sum(prof.exclusive.values())
            row = {k: prof.exclusive.get(k, 0.0) for k in CATEGORIES if k != 'other'}
            row['other'] = out['wall_total']-accounted
            breakdown[(case['orbit_id'], engine)] = dict(
                seconds=row, calls={k: prof.calls.get(k, 0) for k in CATEGORIES},
                instrumented_wall=out['wall_total'])
    print('разбивка времени готова', flush=True)

    rows = []
    print('\norbit_id | A: шагов, с | C: шагов, перезапусков, с | C/A по времени | исходы A/C')
    totals = defaultdict(float)
    for case in cases:
        oid = case['orbit_id']
        a, c = results[(oid, 'dop853_jit')], results[(oid, 'hierarchical_cached')]
        ta, tc = min(clean[oid]['dop853_jit']), min(clean[oid]['hierarchical_cached'])
        totals['A'] += ta
        totals['C'] += tc
        restarts = breakdown[(oid, 'hierarchical_cached')]['calls']['integrator_restart']
        agree = agreement(a, c, case)
        print(f"{oid} | {a['steps']}, {ta:.3f} | {c['steps']}, {restarts}, {tc:.3f} | {tc/ta:.2f} | "
              f"{agree['outcome_A']}/{agree['outcome_C']}")
        rows.append(dict(orbit_id=oid, host=case['host'], period_seconds=case['period_seconds'],
            horizon_seconds=horizons[oid],
            A=dict(wall_min=ta, walls=clean[oid]['dop853_jit'], steps=a['steps'], nfev=a['nfev'],
                   run_status=a['run_status'], last_valid_time=a['last_valid_time'],
                   breakdown=breakdown[(oid, 'dop853_jit')]),
            C=dict(wall_min=tc, walls=clean[oid]['hierarchical_cached'], steps=c['steps'],
                   nfev=c['nfev'], run_status=c['run_status'], last_valid_time=c['last_valid_time'],
                   breakdown=breakdown[(oid, 'hierarchical_cached')]),
            agreement=agree))
    prep = sum(c.wall_seconds for c in caches.values())
    print(f"ИТОГО (сумма минимумов): A {totals['A']:.2f} с; C {totals['C']:.2f} с "
          f"(+ подготовка кешей {prep:.2f} с, однократно на хозяина); C/A = {totals['C']/totals['A']:.2f}")

    print('\nДоля времени по категориям (инструментированный прогон, сумма по 12 случаям):')
    for engine in ENGINES:
        sums = defaultdict(float)
        for (oid, eng), b in breakdown.items():
            if eng == engine:
                for k, v in b['seconds'].items():
                    sums[k] += v
        total = sum(sums.values())
        parts = ', '.join(f'{k} {100*sums[k]/total:.0f}%' for k in CATEGORIES if sums[k] > 0.005*total)
        print(f'  {SHORT[engine]} ({total:.1f} с): {parts}')
    return dict(rows=rows, totals_min_seconds=dict(totals), cache_preparation_seconds=prep,
                caches={h: dict(segments=len(c.segments), bytes=c.bytes,
                                prep_wall_seconds=c.wall_seconds) for h, c in caches.items()})


def scaling(cases, args):
    logical = os.cpu_count() or 1
    physical = psutil.cpu_count(logical=False) or logical
    levels = sorted({1, 2, max(1, physical//2), physical, logical} if not args.levels
                    else set(args.levels))
    replicate = max(1, math.ceil(2*max(levels)/len(cases)))
    ordered = sorted(cases, key=lambda c: -c['period_seconds'])  # длинные задания первыми
    print(f'\nмасштабирование: уровни {levels}, заданий на уровень {replicate*len(cases)} '
          f'на движок, горизонт {args.scaling_periods} периодов', flush=True)
    warmup()
    out = {}
    for engine in ENGINES:
        jobs = [dict(case=c, engine=engine, periods=args.scaling_periods, wall_seconds=36000.,
                     memory_bytes=4*2**30, reuse_cache=True, repeat=r)
                for r in range(replicate) for c in ordered]
        base = None
        for workers in levels:
            started = time.perf_counter()
            with mp.get_context('spawn').Pool(workers, initializer=_init) as pool:
                results = pool.map(run_case, jobs, chunksize=1)
            wall = time.perf_counter()-started
            bad = sum(r['run_status'] != 'completed' for r in results)
            peak = max(r['memory_peak_bytes'] for r in results)
            base = base or wall
            speedup = base/wall
            out.setdefault(engine, []).append(dict(workers=workers, wall_seconds=wall, jobs=len(jobs),
                speedup=speedup, efficiency=speedup/workers, not_completed=bad,
                worker_memory_peak_bytes=peak))
            print(f'  {SHORT[engine]} W={workers:>3}: {wall:7.1f} с, ускорение x{speedup:.2f}, '
                  f'эффективность {100*speedup/workers:.0f}%, пик RSS процесса {peak/2**20:.0f} МиБ'
                  + (f', НЕ завершено {bad}' if bad else ''), flush=True)
    return dict(levels=levels, replicate=replicate, periods=args.scaling_periods, results=out)


def _init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--periods', type=float, default=20.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--scaling', action='store_true', help='только замер масштабирования')
    parser.add_argument('--scaling-periods', type=float, default=20.0)
    parser.add_argument('--levels', type=int, nargs='*', default=None)
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    mode = NumericMode()
    _, cases = load_cases(ROOT)
    cases = performance_cases(cases)[:args.limit]
    info = cpu_info()
    lscpu = info.get('lscpu', {})
    print(f"CPU: {lscpu.get('Model name', platform.processor())}; логических {info['logical_cpus']}, "
          f"физических ядер {info['physical_cores']}; L2 {lscpu.get('L2 cache', lscpu.get('L2', '?'))}, L3 {lscpu.get('L3 cache', lscpu.get('L3', '?'))}; "
          f"RAM {info['memory_total_gib']} ГиБ", flush=True)
    print(f'случаев: {len(cases)}', flush=True)
    warm = warmup()

    report = dict(task='W2-T008', kind='machine_benchmark_not_scientific_result',
                  numeric=mode.__dict__, machine=info, warmup_seconds=warm, periods=args.periods,
                  repeats=args.repeats, cases=[c['orbit_id'] for c in cases])
    if args.scaling:
        report['scaling'] = scaling(cases, args)
    else:
        report['single_process'] = single_process(cases, args, mode)

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    report['utc'] = stamp
    kind = 'scaling' if args.scaling else 'single'
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-T008_ac_{kind}_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
