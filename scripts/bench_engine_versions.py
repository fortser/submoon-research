"""W2-T008: замер «до/после» оптимизации событий (этап D) на движке A.

Варианты: v0 — дословная копия прежнего engine_compare/событий
(tests/reference), current — текущий код. 12 performance_cases, одинаковый
горизонт, повторы с чередованием порядка. Корректность: результаты обязаны
совпасть побитно (JSON всех полей, кроме времени исполнения). Для движка C
выполняется по одному прогону на случай — только проверка совпадения.
Затем разбивка времени текущего A по категориям (как в bench_engines_ac).
Машинный замер, не научный результат.

    ~/venvs/submoon-w2/bin/python scripts/bench_engine_versions.py --periods 20 --repeats 3
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))
sys.path.insert(0, str(ROOT/'tests'/'reference'))

import numpy as np  # noqa: E402

import engine_compare_v0  # noqa: E402
from bench_engines_ac import CATEGORIES, Profiler, cpu_info, prepare_caches  # noqa: E402
from submoon_research.dynamics import engine_compare  # noqa: E402
from submoon_research.dynamics.engine_compare import NumericMode  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases, performance_cases, warmup  # noqa: E402

VARIANTS = {'v0': engine_compare_v0.integrate_engine, 'current': engine_compare.integrate_engine}
TIMING_KEYS = ('wall_seconds', 'cpu_seconds', 'cache_preparation_wall_seconds',
               'cache_preparation_cpu_seconds')


def run(variant, engine, case, horizon, mode, cache=None):
    started = time.perf_counter()
    out = VARIANTS[variant](engine, case['state'], case['gms'], case['figures'], case['radii'],
        horizon, period=case['period_seconds'], mode=mode, hill_km=case.get('hill_km'),
        escape_window=case.get('escape_window'), times=np.linspace(0, horizon, 65),
        cache=cache if engine == 'hierarchical_cached' else None)
    wall = time.perf_counter()-started
    comparable = {k: v for k, v in out.items() if k not in TIMING_KEYS}
    return wall, json.dumps(comparable, sort_keys=True, default=float), out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--periods', type=float, default=20.0)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--skip-c', action='store_true')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    mode = NumericMode()
    _, cases = load_cases(ROOT)
    cases = performance_cases(cases)[:args.limit]
    info = cpu_info()
    print(f"CPU: {info.get('lscpu', {}).get('Model name', '?')}; случаев {len(cases)}, "
          f"{args.periods} периодов, повторов {args.repeats}", flush=True)
    warmup()
    horizons = {c['orbit_id']: c['period_seconds']*args.periods for c in cases}
    for variant in VARIANTS:  # прогрев путей кода
        run(variant, 'dop853_jit', cases[0], cases[0]['period_seconds'], mode)

    walls = defaultdict(lambda: defaultdict(list))
    mismatches, steps = [], {}
    for repeat in range(args.repeats):
        order = list(VARIANTS) if repeat % 2 == 0 else list(VARIANTS)[::-1]
        for case in cases:
            outputs = {}
            for variant in order:
                wall, text, out = run(variant, 'dop853_jit', case, horizons[case['orbit_id']], mode)
                walls[case['orbit_id']][variant].append(wall)
                outputs[variant] = text
                steps[case['orbit_id']] = out['steps']
            if outputs['v0'] != outputs['current']:
                mismatches.append(dict(engine='A', orbit_id=case['orbit_id'], repeat=repeat))
        print(f'A: повтор {repeat+1}/{args.repeats} готов', flush=True)

    c_checked = 0
    if not args.skip_c:
        caches = prepare_caches(cases, horizons, mode)
        for case in cases:
            texts = [run(v, 'hierarchical_cached', case, horizons[case['orbit_id']], mode,
                         caches[case['host']])[1] for v in VARIANTS]
            c_checked += 1
            if texts[0] != texts[1]:
                mismatches.append(dict(engine='C', orbit_id=case['orbit_id']))
        print(f'C: проверено совпадение на {c_checked} случаях', flush=True)

    breakdown = defaultdict(float)
    calls = defaultdict(int)
    total_steps = 0
    for case in cases:
        with Profiler() as prof:
            wall, _, out = run('current', 'dop853_jit', case, horizons[case['orbit_id']], mode)
        for k, v in prof.exclusive.items():
            breakdown[k] += v
        for k, v in prof.calls.items():
            calls[k] += v
        breakdown['other'] += wall-sum(prof.exclusive.values())
        total_steps += out['steps']

    print('\norbit_id | шагов | v0, с (мин) | current, с (мин) | ускорение')
    totals = defaultdict(float)
    for case in cases:
        oid = case['orbit_id']
        best = {v: min(walls[oid][v]) for v in VARIANTS}
        for v in VARIANTS:
            totals[v] += best[v]
        print(f"{oid} | {steps[oid]} | {best['v0']:.3f} | {best['current']:.3f} | x{best['v0']/best['current']:.2f}")
    print(f"ИТОГО A: {totals['v0']:.2f} с -> {totals['current']:.2f} с, x{totals['v0']/totals['current']:.2f}; "
          f"мс/шаг: {1000*totals['v0']/sum(steps.values()):.3f} -> {1000*totals['current']/sum(steps.values()):.3f}")
    total = sum(breakdown.values())
    print('Разбивка текущего A (мкс/шаг): ' + ', '.join(
        f'{k} {1e6*breakdown[k]/total_steps:.0f}' for k in CATEGORIES if breakdown.get(k, 0) > 0.005*total))
    print('побитное совпадение v0/current:', 'ДА' if not mismatches else f'НЕТ {mismatches}')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-T008_versions_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(task='W2-T008', stage='D', kind='machine_benchmark_not_scientific_result',
        utc=stamp, machine=info, periods=args.periods, repeats=args.repeats, numeric=mode.__dict__,
        walls={k: dict(v) for k, v in walls.items()}, steps=steps, totals_min_seconds=dict(totals),
        c_cases_checked=c_checked, bitwise_identical=not mismatches, mismatches=mismatches,
        breakdown_seconds=dict(breakdown), breakdown_calls=dict(calls), breakdown_steps=total_steps),
        ensure_ascii=False, indent=1, default=float), encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0 if not mismatches else 1


if __name__ == '__main__':
    raise SystemExit(main())
