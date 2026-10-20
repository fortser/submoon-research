"""OPT-08/09: причины guards, ограниченная трасса, native/physical restart.

Synthetic J2-контроль, не воспроизведение исторического отказа W2-I034.
"""
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from submoon_research.contracts.p2_files import canonical, file_digest
from submoon_research.diagnostics.ias15_replay import (
    StepTrace, guard_reason, load_snapshot, save_snapshot,
)
from submoon_research.dynamics.native_ias15 import NativeIAS15
from test_p2_file_contracts import load

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('message,left,right,reason', [
    ('Граница IAS15 должна быть впереди', 2., 2., 'bound_not_ahead'),
    ('IAS15 нарушил временную границу', 2., 2., 'no_time_advance'),
    ('IAS15 нарушил временную границу', 2., 5., 'time_bound_exceeded'),
    ('Фактический шаг IAS15 не согласован с меткой времени', 2., 3., 'inconsistent_dt_done'),
    ('Полином IAS15 не воспроизвёл конец принятого шага', 2., 3., 'endpoint_mismatch'),
    ('unknown', 2., 2., 'other_exception'),
])
def test_guard_reasons(message, left, right, reason):
    assert guard_reason(ArithmeticError(message), left, right, 3.) == reason


def synthetic():
    pytest.importorskip('reboundx')
    figures = [dict(index=0, j2=0.01, radius=1., pole=[0., 0., 1.])]
    native = NativeIAS15([[0., 0., 0., 0., 0., 0.], [10., 0., 0., 0., math.sqrt(.1), .01]],
                         [1.], figures, initial_dt=.01)
    replay = load('replay_ias15_snapshot')
    identity = dict(job_fingerprint='synthetic-J2', B_implementation=replay.b_fingerprint(),
                    years=1000./(365.25*86400.), period_seconds=2*math.pi*math.sqrt(1000.),
                    gm_host=1., step_fraction=.025)
    return native, figures, identity, replay


def test_native_snapshot_across_process_and_physical_restart(tmp_path):
    native, figures, identity, replay = synthetic()
    for _ in range(20):
        replay.step_record(native, identity)
    snapshot = save_snapshot(native, tmp_path/'snapshot', figures=figures, identity=identity)
    initial = native.absolute_state().copy()
    physical, _ = load_snapshot(snapshot['manifest'], snapshot['sha256'], expected_identity=identity, physical_only=True)
    assert np.array_equal(physical.absolute_state(), initial)
    expected = [replay.step_record(native, identity) for _ in range(32)]
    env = dict(os.environ, OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
    for kind in ('native', 'physical'):
        out = tmp_path/(kind+'.json')
        args = [sys.executable, str(ROOT/'scripts/replay_ias15_snapshot.py'), '--snapshot', snapshot['manifest'],
                '--snapshot-sha256', snapshot['sha256'], '--job-fingerprint', identity['job_fingerprint'],
                '--steps', '32', '--output', str(out)]
        if kind == 'physical':
            args += ['--physical-only']
        proc = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stdout+proc.stderr
        report = json.loads(out.read_text(encoding='utf-8'))
        assert report['failure'] is None and len(report['records']) == 32
        if kind == 'native':
            assert canonical(report['records']) == canonical(expected)
        else:
            assert report['restart_kind'] == 'physical_state_new_adaptive_history'
    with pytest.raises(ValueError, match='SHA'):
        load_snapshot(snapshot['manifest'], '0'*64, expected_identity=identity)
    with pytest.raises(ValueError, match='Чужой'):
        load_snapshot(snapshot['manifest'], snapshot['sha256'], expected_identity={'wrong': True})
    path = tmp_path/'snapshot/reboundx.bin'
    path.write_bytes(path.read_bytes()+b'x')
    with pytest.raises(ValueError, match='SHA'):
        load_snapshot(snapshot['manifest'], snapshot['sha256'], expected_identity=identity)


def test_trace_passive_and_bounded(tmp_path):
    a, figures, identity, replay = synthetic()
    b, _, _, _ = synthetic()
    trace = StepTrace(directory=tmp_path/'trace', figures=figures, identity=identity,
                      ring=3, snapshot_at=0., max_steps=8)
    for _ in range(8):
        sa = trace.step(NativeIAS15.step, a, 1000., 2.)
        sb = b.step(1000., 2.)
        assert np.array_equal(sa.coefficients, sb.coefficients)
        assert np.array_equal(a.absolute_state(), b.absolute_state())
    report = trace.report()
    assert len(report['records']) == 3 and report['steps'] == 8 and report['snapshot']['time'] == 0.
    assert report['records'][0]['requested_dt'] > 0
    assert report['records'][0]['adaptation_after']['min_dt'] == 0.
    with pytest.raises(TimeoutError, match='diagnostic_budget'):
        trace.step(NativeIAS15.step, a, 1000., 2.)
    manifest = Path(report['snapshot']['manifest'])
    assert file_digest(manifest) == report['snapshot']['sha256']
    assert len(list((tmp_path/'trace').iterdir())) == 1


def test_no_advance_guard_is_not_removed(tmp_path):
    native, figures, identity, _ = synthetic()
    native.sim.t = float(2**35)
    native.sim.dt = float(np.spacing(native.sim.t))/4
    trace = StepTrace(directory=tmp_path/'trace', figures=figures, identity=identity, ring=3)
    # Synthetic reproduction of the immediate mechanism only, not its historical cause.
    with pytest.raises(ArithmeticError, match='временную границу'):
        trace.step(NativeIAS15.step, native, float(2**35)+1., native.sim.dt)
    report = trace.report()
    assert report['failure']['reason'] == 'no_time_advance'
    assert report['records'][-1]['actual_dt'] == 0.
    assert report['snapshot']['time'] == float(2**35)
