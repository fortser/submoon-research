"""Изолированный restart compiled DOP853 в другом процессе, синтетика."""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine


def test_compiled_restart_other_process_with_escape_tracker(tmp_path):
    initial = np.array([[0., 0., 0., 0., 0., 0.], [1., 0., 0., 0., 1., 0.]])
    args = dict(period=2 * math.pi, mode=NumericMode.tight(), hill_km=10., escape_window=3.)
    checkpoints = []

    def interrupt(value):
        if value['time'] > .8:
            checkpoints.append(json.loads(json.dumps(value)))
            raise TimeoutError('synthetic interruption')

    full = integrate_engine('dop853_jit', initial, [1.], [], [.01], 2., **args)
    partial = integrate_engine('dop853_jit', initial, [1.], [], [.01], 2., progress=interrupt, **args)
    assert full['run_status'] == 'completed' and partial['run_status'] == 'partial'
    cp = checkpoints[0]
    assert cp['escape'] is not None
    cp_path, result_path = tmp_path / 'checkpoint.json', tmp_path / 'result.json'
    cp_path.write_text(json.dumps(cp), encoding='utf-8')
    program = '''
import json, math, sys
import numpy as np
from pathlib import Path
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine
initial = np.array([[0.,0.,0.,0.,0.,0.],[1.,0.,0.,0.,1.,0.]])
checkpoint = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
result = integrate_engine('dop853_jit', initial, [1.], [], [.01], 2., period=2*math.pi,
    mode=NumericMode.tight(), hill_km=10., escape_window=3., resume=checkpoint)
Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf-8')
'''
    env = os.environ.copy()
    env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', NUMBA_NUM_THREADS='1',
               PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    child = subprocess.run([sys.executable, '-c', program, str(cp_path), str(result_path)],
                           capture_output=True, text=True, timeout=90, env=env, check=False)
    assert child.returncode == 0, child.stderr
    resumed = json.loads(result_path.read_text(encoding='utf-8'))
    assert resumed['run_status'] == 'completed' and resumed['event'] == full['event']
    assert resumed['checkpoint']['escape'] == full['checkpoint']['escape']
    np.testing.assert_allclose(resumed['final_state'], full['final_state'], atol=1e-8, rtol=0.)
    bad = cp | {'contract_sha256': 'incorrect'}
    try:
        integrate_engine('dop853_jit', initial, [1.], [], [.01], 2., resume=bad, **args)
    except ValueError as exc:
        assert 'контракт' in str(exc)
    else:
        raise AssertionError('Invalid checkpoint contract was accepted')
