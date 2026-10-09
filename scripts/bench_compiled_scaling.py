"""W2-T008: масштабирование прототипа скомпилированного DOP853 и scipy-пути A по процессам.

Длинные задания внутренних (выживающих) орбит performance_cases, одинаковый
список на всех уровнях параллелизма. Пул spawn; каждый процесс прогревает Numba
в инициализаторе, а отсчёт начинается после того, как все процессы готовы, так
что время запуска пула и компиляции в замер не входит (печатается отдельно).
Для сравнимой длительности заданий горизонт scipy-пути короче (по умолчанию
100 периодов против 3000), сравнивается эффективность масштабирования, а не
абсолютная скорость. Машинный замер, не научный результат.

    ~/venvs/submoon-w2/bin/python scripts/bench_compiled_scaling.py
"""
from __future__ import annotations

import argparse
import json
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
import psutil  # noqa: E402

from bench_engines_ac import cpu_info  # noqa: E402
from submoon_research.dynamics.compiled_dop853 import PROTOTYPE_VERSION, integrate_compiled  # noqa: E402
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases, performance_cases, warmup  # noqa: E402


def _integrate(engine, case, periods):
    horizon = case['period_seconds']*periods
    kwargs = dict(period=case['period_seconds'], mode=NumericMode(), hill_km=case.get('hill_km'),
                  escape_window=case.get('escape_window'), times=np.linspace(0, horizon, 9))
    if engine == 'compiled':
        return integrate_compiled(case['state'], case['gms'], case['figures'], case['radii'],
                                  horizon, **kwargs)
    return integrate_engine('dop853_jit', case['state'], case['gms'], case['figures'],
                            case['radii'], horizon, **kwargs)


def _init(case):
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()
    _integrate('compiled', case, 1.0)
    _integrate('scipy_A', case, 1.0)


def _ping(_):
    time.sleep(0.3)
    return os.getpid()


def _job(job):
    started = time.perf_counter()
    out = _integrate(job['engine'], job['case'], job['periods'])
    return dict(orbit_id=job['case']['orbit_id'], wall=time.perf_counter()-started,
                steps=out['steps'], status=out['run_status'], outcome=out['physical_outcome'],
                simulated_seconds=out['last_valid_time'], pid=os.getpid(),
                rss=psutil.Process().memory_info().rss)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--levels', type=int, nargs='*', default=None)
    parser.add_argument('--replicas', type=int, default=3)
    parser.add_argument('--compiled-periods', type=float, default=3000.0)
    parser.add_argument('--scipy-periods', type=float, default=100.0)
    parser.add_argument('--engines', nargs='*', default=['compiled', 'scipy_A'])
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    _, cases = load_cases(ROOT)
    inner = [c for c in performance_cases(cases) if c.get('radial') == 'inner']
    info = cpu_info()
    logical = os.cpu_count() or 1
    physical = psutil.cpu_count(logical=False) or logical
    levels = sorted(set(args.levels) if args.levels else {1, 2, max(1, physical//2), physical, logical})
    print(f"прототип v{PROTOTYPE_VERSION}; CPU: {info.get('lscpu', {}).get('Model name', '?')}; "
          f"ядер {physical}, потоков {logical}; уровни {levels}; заданий на уровень "
          f"{len(inner)*args.replicas}", flush=True)
    periods = {'compiled': args.compiled_periods, 'scipy_A': args.scipy_periods}
    results = {}
    for engine in args.engines:
        jobs = [dict(engine=engine, case=c, periods=periods[engine])
                for _ in range(args.replicas) for c in inner]
        base = None
        results[engine] = []
        for workers in levels:
            started = time.perf_counter()
            with mp.get_context('spawn').Pool(workers, initializer=_init, initargs=(inner[0],)) as pool:
                pool.map(_ping, range(2*workers), chunksize=1)  # все процессы инициализированы
                startup = time.perf_counter()-started
                t0 = time.perf_counter()
                rows = pool.map(_job, jobs, chunksize=1)
                wall = time.perf_counter()-t0
            busy = sum(r['wall'] for r in rows)
            base = base or wall
            speedup = base/wall
            years = sum(r['simulated_seconds'] for r in rows)/31557600.0
            bad = sum(r['status'] != 'completed' for r in rows)
            results[engine].append(dict(workers=workers, wall_seconds=wall, startup_seconds=startup,
                busy_seconds=busy, speedup=speedup, efficiency=speedup/workers,
                model_years_per_second=years/wall, mean_job_seconds=busy/len(rows),
                peak_worker_rss_mib=max(r['rss'] for r in rows)/2**20, not_completed=bad,
                distinct_workers=len({r['pid'] for r in rows})))
            print(f'  {engine:8} W={workers:>2}: {wall:7.1f} с (запуск пула {startup:.1f} с), '
                  f'ускорение x{speedup:.2f}, эффективность {100*speedup/workers:.0f}%, '
                  f'{years/wall:.3f} модельных лет/с, среднее задание {busy/len(rows):.2f} с, '
                  f'RSS {max(r["rss"] for r in rows)/2**20:.0f} МиБ' + (f', НЕ завершено {bad}' if bad else ''),
                  flush=True)
    if 'compiled' in results and 'scipy_A' in results:
        for a, b in zip(results['compiled'], results['scipy_A']):
            print(f"W={a['workers']}: пропускная способность прототип/scipy = "
                  f"x{a['model_years_per_second']/b['model_years_per_second']:.1f}")

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-T008_scaling_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(task='W2-T008', stage='F', kind='machine_benchmark_not_scientific_result',
        utc=stamp, prototype_version=PROTOTYPE_VERSION, machine=info, levels=levels,
        replicas=args.replicas, periods=periods, cases=[c['orbit_id'] for c in inner], results=results),
        ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
