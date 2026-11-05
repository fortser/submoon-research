"""W2-I034/W2-T043: B starts without centre-of-mass drift. Synthetic checks only."""
import math

import numpy as np
import pytest

from submoon_research.dynamics import engine_compare
from submoon_research.dynamics.engine_compare import ias15_start_state, integrate_engine

# Host at rest at the origin (B input convention), a heavy fast "planet", a massless probe.
GMS = [1., 1000.]
V_PLANET = math.sqrt(1001./100.)
INITIAL = np.array([[0., 0., 0., 0., 0., 0.],
                    [100., 0., 0., 0., V_PLANET, 0.],
                    [1., 0., 0., 0., 1., 0.02]])
RADII = [0.01, 1.]


def test_start_state_keeps_positions_and_removes_cm_velocity():
    out = ias15_start_state(INITIAL, GMS).reshape(-1, 6)
    assert np.array_equal(out[:, :3], INITIAL[:, :3])
    v_cm = (np.r_[GMS, 0.][:, None]*out[:, 3:]).sum(axis=0)/sum(GMS)
    assert np.max(np.abs(v_cm)) < 1e-14
    rel = lambda s: s[:, None, 3:]-s[None, :, 3:]  # noqa: E731
    assert np.max(np.abs(rel(out)-rel(INITIAL))) <= 4*np.spacing(np.max(np.abs(out[:, 3:])))
    assert INITIAL[1, 4] == V_PLANET  # input untouched


def test_start_state_rejects_bad_inputs():
    with pytest.raises(ValueError):
        ias15_start_state(INITIAL[:2], GMS)
    with pytest.raises(ValueError):
        ias15_start_state(INITIAL, [1., 0.])
    bad = INITIAL.copy()
    bad[2, 0] = np.nan
    with pytest.raises(ValueError):
        ias15_start_state(bad, GMS)


def _recording(monkeypatch):
    pytest.importorskip('reboundx')
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    starts, maxima = [], []

    class Recording(NativeIAS15):
        def __init__(self, state, *args, **kwargs):
            starts.append(np.asarray(state, dtype=float).reshape(-1, 6).copy())
            super().__init__(state, *args, **kwargs)
            maxima.append(0.)

        def step(self, *args, **kwargs):
            segment = super().step(*args, **kwargs)
            maxima[-1] = max(maxima[-1], float(np.max(np.abs(self.absolute_state()[:, :3]))))
            return segment

    monkeypatch.setattr(engine_compare, 'NativeIAS15', Recording)
    return starts, maxima


def _run(horizon, **kwargs):
    return integrate_engine('ias15_reboundx', INITIAL, GMS, [], RADII, horizon, period=2*math.pi, **kwargs)


def test_production_path_uses_drift_free_start_and_bounded_coordinates(monkeypatch):
    starts, maxima = _recording(monkeypatch)
    out = _run(500.)
    assert out['run_status'] == 'completed'
    assert np.array_equal(starts[0][:, :3], INITIAL[:, :3])
    assert np.max(np.abs((np.r_[GMS, 0.][:, None]*starts[0][:, 3:]).sum(axis=0))) < 1e-10
    # Without the boost the system would drift by ~|v_cm|*T ~ 1580 units.
    assert maxima[0] < 250.


def test_resume_from_checkpoint_also_starts_without_drift(monkeypatch):
    starts, _ = _recording(monkeypatch)
    checkpoints = []
    first = _run(60., progress=checkpoints.append)
    assert first['run_status'] == 'completed' and len(checkpoints) > 10
    middle = checkpoints[len(checkpoints)//2]
    resumed = _run(60., resume=middle)
    assert resumed['run_status'] == 'completed'
    state = np.asarray(middle['state']).reshape(-1, 6)
    assert np.array_equal(starts[-1][:, :3], state[:, :3])
    assert np.max(np.abs((np.r_[GMS, 0.][:, None]*starts[-1][:, 3:]).sum(axis=0))) < 1e-10


def test_relative_motion_is_frame_independent(monkeypatch):
    pytest.importorskip('reboundx')
    times = np.linspace(0., 30., 31)
    new = _run(30., times=times)
    monkeypatch.setattr(engine_compare, 'ias15_start_state', lambda state, gms: np.asarray(state).ravel())
    old = _run(30., times=times)
    assert new['physical_outcome'] == old['physical_outcome'] == 'survived'
    for a, b in zip(new['samples'], old['samples']):
        assert a['time'] == b['time']
        da = np.asarray(a['state']).reshape(-1, 6)[-1]-np.asarray(b['state']).reshape(-1, 6)[-1]
        assert np.linalg.norm(da[:3]) < 1e-8 and np.linalg.norm(da[3:]) < 1e-8


def test_start_state_rejects_non_finite_gm():
    with pytest.raises(ValueError):
        ias15_start_state(INITIAL, [1., float('nan')])


def test_other_engine_contract_is_unchanged():
    """W2-I039 changes only the B contract; A keeps its historical contract fingerprint."""
    from dataclasses import asdict
    out = integrate_engine('dop853_jit', INITIAL, GMS, [], RADII, 5., period=2*math.pi)
    expected = engine_compare.fingerprint(dict(
        initial=INITIAL.tolist(), gms=list(map(float, GMS)), figures=[], radii=list(map(float, RADII)),
        horizon=5., period=2*math.pi, mode=asdict(engine_compare.NumericMode()), engine='dop853_jit',
        hill=None, escape_window=None))
    assert out['checkpoint']['contract_sha256'] == expected


def test_b_checkpoint_of_other_implementation_is_rejected(monkeypatch):
    pytest.importorskip('reboundx')
    checkpoints = []
    _run(20., progress=checkpoints.append)
    middle = checkpoints[len(checkpoints)//2]
    assert _run(20., resume=middle)['run_status'] == 'completed'  # same implementation: accepted
    monkeypatch.setattr(engine_compare, 'b_implementation_fingerprint', lambda: 'other-implementation')
    with pytest.raises(ValueError, match='контракт'):
        _run(20., resume=middle)


def test_b_implementation_fingerprint_matches_stand():
    pytest.importorskip('reboundx')
    from test_p2_file_contracts import load
    assert engine_compare.b_implementation_fingerprint() == load('admission_p2_horizons').b_fingerprint()
