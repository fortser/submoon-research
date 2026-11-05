"""Короткие синтетические контроли полной орбиты для W2-T068."""
from __future__ import annotations

import hashlib
import json
import math
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.dynamics.native_ias15 import MassiveCache  # noqa: E402

OUT = ROOT / 'results/evidence/W2_T068_ias15_warning_20261030/circular.json'


def main():
    if OUT.exists():
        raise SystemExit('Выход существует')
    initial = np.array([[0., 0., 0., 0., 0., 0.], [1., 0., 0., 0., 1., 0.]])
    grid = np.linspace(0., 2., 21)
    mode = NumericMode.tight()
    reference = np.column_stack((np.cos(grid), np.sin(grid), np.zeros_like(grid),
                                 -np.sin(grid), np.cos(grid), np.zeros_like(grid)))
    result = []
    # Одинаковая физика и критерий, различаются только численные режимы кеша.
    for name, engine, cache_limit in [('cached_unlimited', 'hierarchical_cached', None),
                                      ('cached_0_05', 'hierarchical_cached', .05),
                                      ('cached_0_2', 'hierarchical_cached', .2),
                                      ('direct_B', 'ias15_reboundx', None),
                                      ('direct_DOP853', 'dop853_jit', None)]:
        cache = (MassiveCache(initial, [1.], [], epsilon=mode.cache_epsilon,
                              max_step=cache_limit if cache_limit else np.inf)
                 if engine == 'hierarchical_cached' else None)
        with warnings.catch_warnings(record=True) as records:
            warnings.simplefilter('always', RuntimeWarning)
            run = integrate_engine(engine, initial, [1.], [], [.01], 2., period=2*math.pi,
                                   mode=mode, times=grid, cache=cache)
        if run['run_status'] != 'completed' or len(run['samples']) != len(grid):
            raise RuntimeError(f'{name}: {run["run_status"]}: {run["error"]}')
        probe = np.array([item['state'][-6:] for item in run['samples']])
        diff = np.abs(probe - reference)
        result.append(dict(name=name, engine=engine, cache_max_step=cache_limit, n_samples=len(grid),
            max_component_error=float(diff.max()), final_error=float(diff[-1].max()),
            physical_outcome=run['physical_outcome'], final_state=run['final_state'],
            accepted_steps=run['steps'], warnings=[str(r.message) for r in records]))
    doc = dict(synthetic=True, units='dimensionless GM=1 and period=2π', results=result,
               script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               limitation='Одно аналитическое двухтельное состояние; не допуск реальной многотельной физики.')
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(doc, f, indent=2, ensure_ascii=False, allow_nan=False)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
