"""W2-I035: малые векторы без BLAS — генерация стартов не зависит от ядра OpenBLAS."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from submoon_research.exact_linalg import dot, matmul, matvec, norm

ROOT = Path(__file__).resolve().parents[2]


def test_exactly_rounded_sums_and_shapes():
    assert dot([1e16, 1.0, -1e16], [1.0, 1.0, 1.0]) == 1.0   # наивная сумма даёт 0
    assert norm([3.0, 4.0]) == 5.0
    m = np.array([[1.0, 2.0], [3.0, 4.0]])
    assert matvec(m, [1.0, 1.0]).tolist() == [3.0, 7.0]
    assert matmul(m, np.eye(2)).tolist() == m.tolist()
    with pytest.raises(ValueError):
        dot([1.0], [1.0, 2.0])
    with pytest.raises(ValueError):
        matvec(m, [1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        matmul(m, np.ones((3, 3)))


PROBE = r'''
import json, sys
sys.path.insert(0, sys.argv[1] + "/scripts"); sys.path.insert(0, sys.argv[1] + "/src")
import plan_p2_2
points = plan_p2_2.sample(2)
doms = plan_p2_2.domains()
print(json.dumps(dict(points=[{k: (float(v).hex() if isinstance(v, float) else v) for k, v in p.items()} for p in points],
                      hill={h: float(d.hill_km).hex() for h, (d, _) in doms.items()},
                      basis={h: [float(x).hex() for x in b.ravel()] for h, (_, b) in doms.items()})))
'''


def cpu_has(flag):
    try:
        return any(line.startswith('flags') and flag in line.split() for line in open('/proc/cpuinfo', encoding='utf-8'))
    except OSError:
        return False


def test_generation_independent_of_openblas_kernel():
    """Одинаковые стартовые элементы и базисы при разных ядрах OpenBLAS (W2-I035: Япет на i5 отличался)."""
    kernels = ['Nehalem', 'Sandybridge'] + (['Haswell'] if cpu_has('avx2') else [])
    outputs = {}
    for kernel in kernels:
        env = dict(os.environ, OPENBLAS_CORETYPE=kernel, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1')
        result = subprocess.run([sys.executable, '-c', PROBE, str(ROOT)], env=env, capture_output=True,
                                text=True, timeout=300, check=True)
        outputs[kernel] = json.loads(result.stdout.strip().splitlines()[-1])
    first = outputs[kernels[0]]
    assert len(first['points']) == 18
    for kernel in kernels[1:]:
        assert outputs[kernel] == first, kernel


def test_legacy_mode_repeats_numpy_and_restores():
    from submoon_research.exact_linalg import LEGACY, NUMERICS_VERSION, mode, numerics
    rng = np.random.default_rng(7)
    a, b, m = rng.normal(size=3), rng.normal(size=3), rng.normal(size=(3, 3))
    assert mode() == NUMERICS_VERSION
    with numerics(LEGACY):
        assert dot(a, b) == float(np.dot(a, b)) and norm(a) == float(np.linalg.norm(a))
        assert np.array_equal(matvec(m, a), m @ a) and np.array_equal(matmul(m, m), m @ m)
    assert mode() == NUMERICS_VERSION
    with pytest.raises(ValueError):
        with numerics('fast'):
            pass
