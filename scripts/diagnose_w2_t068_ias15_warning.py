"""W2-T068: синтетическая диагностика предупреждений IAS15, не production."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.dynamics.native_ias15 import MassiveCache  # noqa: E402

OUT = ROOT / 'results/evidence/W2_T068_ias15_warning_20261030/cache_controls.json'


def run(name, initial, gms, limit, epsilon=1e-13):
    cache = MassiveCache(np.array(initial, dtype=float), np.array(gms), [], epsilon=epsilon, max_step=limit)
    captured = []
    with warnings.catch_warnings(record=True) as records:
        warnings.simplefilter('always', RuntimeWarning)
        # Перехват на уровне одного шага позволяет указать время и размер шага.
        from submoon_research.dynamics.native_ias15 import NativeIAS15
        cache.native = NativeIAS15(cache.initial, cache.gms, cache.figures, epsilon=epsilon)
        residual_max = 0.0
        max_steps = 10000
        while cache.end < 2.0 and len(cache.segments) < max_steps:
            before = len(records)
            native = cache.native
            t0, proposed = float(native.sim.t), float(native.sim.dt)
            segment = native.step(2.0, limit)
            residual_max = max(residual_max, native.last_endpoint_residual)
            for record in records[before:]:
                captured.append(dict(message=str(record.message), t_before=t0, t_after=float(native.sim.t),
                    proposed_dt=proposed, applied_max_dt=limit if np.isfinite(limit) else None,
                    accepted_dt=float(native.sim.dt_last_done), particles=native.count))
            cache.segments.append(segment)
            cache.ends.append(segment.right)
        if cache.end != 2.0:
            raise RuntimeError('Step limit or incomplete time')
    actual = cache.native.absolute_state()
    if len(gms) == 1:
        expected = np.array(initial, dtype=float)[0].copy()
        expected[:3] += 2 * expected[3:]
        error = float(np.max(np.abs(actual[0] - expected)))
    else:
        error = None
    return dict(name=name, epsilon=epsilon, max_step=limit if np.isfinite(limit) else None,
                n_massive=len(gms), steps=len(cache.segments), final_time=cache.end,
                endpoint_residual_max=residual_max, ballistic_absolute_error=error,
                warnings=captured, final_absolute_state=actual.tolist())


def main():
    if OUT.exists():
        raise SystemExit('Выход существует')
    # Скорости и позиции даны в согласованной безразмерной постановке GM=1.
    host = [[0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 1, 0]]
    drifting = [[0, 0, 0, 0.1, 0, 0], [1, 0, 0, 0.1, 1, 0]]
    two = [[0, 0, 0, 0, 0, 0], [10, 0, 0, 0, 0.32, 0], [1, 0, 0, 0, 1, 0]]
    variants = [run('one_static_unlimited', host, [1.], np.inf),
                run('one_static_max_0_05', host, [1.], .05),
                run('one_static_max_0_2', host, [1.], .2),
                run('one_drifting_unlimited', drifting, [1.], np.inf),
                run('two_massive_unlimited', two, [1., .1], np.inf)]
    doc = dict(schema='w2-t068-synthetic-controls-v1', synthetic=True, scientific_admission=False,
               python=sys.version, platform=platform.platform(), variants=variants,
               script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(doc, f, indent=2, allow_nan=False)
    print(json.dumps(variants, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
