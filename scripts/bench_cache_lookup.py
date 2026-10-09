"""W2-T008: замер эффекта исправления MassiveCache.segment_at на движке C.

Сравнивает прежний поиск сегмента (np.searchsorted по Python-списку) и новый
(курсор + bisect) на одинаковых разработочных случаях performance_cases из
W2_three_engines_v1. Кеш массивной подсистемы готовится заранее и общий для
обоих вариантов, поэтому разница времени относится только к поиску сегмента
и не включает подготовку кеша (её время печатается отдельно).

Корректность: оба варианта обязаны выбрать те же сегменты, поэтому исход,
событие, число шагов, nfev и конечное состояние должны совпасть побитно.
Это машинный замер, не научный результат и не допуск численного режима.

Запуск из корня проекта:
    .venv/Scripts/python.exe scripts/bench_cache_lookup.py --periods 20 --repeats 3
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.dynamics.native_ias15 import MassiveCache  # noqa: E402
from submoon_research.workflows.w2_comparison import load_cases, performance_cases, warmup  # noqa: E402

NEW_SEGMENT_AT = MassiveCache.segment_at


def legacy_segment_at(self, t):
    """Дословная копия реализации до W2-T008 (для сравнения)."""
    index = int(np.searchsorted(self.ends, t, side='right'))
    if index == len(self.segments) and self.segments and t == self.end:
        index -= 1
    if index >= len(self.segments) or t < self.segments[index].left:
        raise ValueError('Состояние вне покрытия кеша')
    return self.segments[index]


VARIANTS = {'legacy_searchsorted': legacy_segment_at, 'cursor_bisect': NEW_SEGMENT_AT}


def run(case, cache, horizon, mode):
    started = time.perf_counter()
    out = integrate_engine('hierarchical_cached', case['state'], case['gms'], case['figures'],
        case['radii'], horizon, period=case['period_seconds'], mode=mode,
        hill_km=case.get('hill_km'), escape_window=case.get('escape_window'),
        times=np.linspace(0, horizon, 65), cache=cache)
    out['wall_total'] = time.perf_counter()-started
    return out


def signature(out):
    return dict(run_status=out['run_status'], physical_outcome=out['physical_outcome'],
        error=out['error'], event=out['event'], steps=out['steps'], nfev=out['nfev'],
        last_valid_time=out['last_valid_time'], final_state=out['final_state'])


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--periods', type=float, default=20.0,
                        help='горизонт в периодах субспутника (по умолчанию 20)')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--limit', type=int, default=None, help='взять первые N случаев')
    parser.add_argument('--output', default=None)
    args = parser.parse_args()

    mode = NumericMode()
    _, cases = load_cases(ROOT)
    cases = performance_cases(cases)[:args.limit]
    print(f'случаев: {len(cases)}, горизонт: {args.periods} периодов, повторов: {args.repeats}')
    warm = warmup()

    horizons = {c['orbit_id']: c['period_seconds']*args.periods for c in cases}
    caches = {}
    for case in cases:
        need = horizons[case['orbit_id']]
        host = case['host']
        if host not in caches or caches[host][1] < need:
            caches[host] = (case, max(need, caches.get(host, (None, 0.0))[1]))
    prepared = {}
    for host, (case, need) in caches.items():
        cache = MassiveCache(case['state'], case['gms'], case['figures'], epsilon=mode.cache_epsilon)
        cache.extend(need, lambda: None)
        prepared[host] = cache
        print(f'кеш {host}: {len(cache.segments)} сегментов, {cache.bytes/2**20:.1f} МиБ, '
              f'подготовка {cache.wall_seconds:.1f} с')

    rows, timings = [], defaultdict(lambda: defaultdict(list))
    mismatches = []
    for repeat in range(args.repeats):
        order = list(VARIANTS) if repeat % 2 == 0 else list(VARIANTS)[::-1]
        for case in cases:
            reference = None
            for name in order:
                MassiveCache.segment_at = VARIANTS[name]
                try:
                    out = run(case, prepared[case['host']], horizons[case['orbit_id']], mode)
                finally:
                    MassiveCache.segment_at = NEW_SEGMENT_AT
                timings[case['orbit_id']][name].append(out['wall_total'])
                sig = signature(out)
                if reference is None:
                    reference = sig
                elif sig != reference:
                    mismatches.append(dict(orbit_id=case['orbit_id'], repeat=repeat))
                rows.append(dict(orbit_id=case['orbit_id'], host=case['host'], variant=name,
                    repeat=repeat, wall_seconds=out['wall_total'], steps=out['steps'],
                    nfev=out['nfev'], run_status=out['run_status'],
                    physical_outcome=out['physical_outcome']))
        print(f'повтор {repeat+1}/{args.repeats} готов')

    print('\norbit_id | шагов | legacy, с (мин) | новый, с (мин) | ускорение')
    total = {name: 0.0 for name in VARIANTS}
    for case in cases:
        t = timings[case['orbit_id']]
        best = {name: min(t[name]) for name in VARIANTS}
        for name in VARIANTS:
            total[name] += best[name]
        steps = next(r['steps'] for r in rows if r['orbit_id'] == case['orbit_id'])
        print(f"{case['orbit_id']} | {steps} | {best['legacy_searchsorted']:.3f} | "
              f"{best['cursor_bisect']:.3f} | x{best['legacy_searchsorted']/best['cursor_bisect']:.2f}")
    print(f"ИТОГО (сумма минимумов): {total['legacy_searchsorted']:.2f} с -> "
          f"{total['cursor_bisect']:.2f} с, x{total['legacy_searchsorted']/total['cursor_bisect']:.2f}")
    print('побитное совпадение исходов и состояний:', 'ДА' if not mismatches else f'НЕТ {mismatches}')

    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    path = Path(args.output) if args.output else ROOT/'scratch/claude'/f'W2-T008_cache_lookup_bench_{stamp}.json'
    if not path.is_absolute():
        path = ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(
        task='W2-T008', kind='machine_benchmark_not_scientific_result', utc=stamp,
        periods=args.periods, repeats=args.repeats, numeric=mode.__dict__, warmup_seconds=warm,
        machine=dict(node=platform.node(), processor=platform.processor(), python=platform.python_version(),
                     platform=platform.platform(), executable=sys.executable),
        caches={h: dict(segments=len(c.segments), bytes=c.bytes, prep_wall_seconds=c.wall_seconds)
                for h, c in prepared.items()},
        totals_min_seconds=total, bitwise_identical=not mismatches, mismatches=mismatches, rows=rows),
        ensure_ascii=False, indent=1), encoding='utf-8')
    print('результат:', path.relative_to(ROOT))
    return 0 if not mismatches else 1


if __name__ == '__main__':
    raise SystemExit(main())
