"""Synthetic W2-P012 checks; not results for the historical B orbit."""
import math

import numpy as np
import pytest

from submoon_research.diagnostics.ias15_memory_controls import (
    BUFFERS, COMPENSATION, MASKS, POLYNOMIAL, VARIANTS, apply_mask, buffer_view,
    first_divergence, integrate_window, memory_state, verify_m0, verify_ma,
)

GMS = [1., 1e-3]
STATE = [[0., 0., 0., 0., 0., 0.],
         [5., 0., 0., 0., math.sqrt(1./5.), 0.],
         [0., 1., 0., -1.15, 0., 0.05]]  # eccentric probe orbit, so dt keeps changing
IDENTITY = dict(period_seconds=2*math.pi, gm_host=1., step_fraction=.025)


def test_masks_are_declared_groups():
    assert tuple(MASKS) == VARIANTS
    assert MASKS['M0'] == dict(buffers=(), dt_last_done=False)
    assert set(MASKS['MA']['buffers']) == set(POLYNOMIAL) | set(COMPENSATION)
    assert MASKS['MA']['dt_last_done'] and MASKS['MD']['dt_last_done']
    assert not set(MASKS['MP']['buffers']) & set(COMPENSATION)
    assert {'at', 'x0', 'v0', 'a0', 'csa0'}.isdisjoint(MASKS['MA']['buffers'])
    with pytest.raises(ValueError, match='Неизвестный'):
        apply_mask(None, 'MX')


def _native(steps=30, t0=0.):
    pytest.importorskip('reboundx')
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    native = NativeIAS15(STATE, GMS, [], epsilon=1e-9, t0=t0, initial_dt=1e-3)
    for _ in range(steps):
        native.step(t0+1e6)
    return native


def test_fresh_null_buffers_are_refused_before_write():
    native = _native(steps=0)
    with pytest.raises(ValueError, match='NULL'):
        buffer_view(native, 'b')


@pytest.mark.parametrize('variant', VARIANTS)
def test_mask_changes_only_declared_fields(variant):
    native = _native()
    before = memory_state(native)
    assert before['buffers']['br']['nonzero'] and before['buffers']['csx']['nonzero']
    assert before['scalars']['dt_last_done'] > 0
    report = apply_mask(native, variant)
    after = memory_state(native)
    for name in BUFFERS:
        if name in MASKS[variant]['buffers']:
            assert after['buffers'][name]['nonzero'] == 0
        else:
            assert after['buffers'][name]['values'] == before['buffers'][name]['values']
    expected_dt = 0. if MASKS[variant]['dt_last_done'] else before['scalars']['dt_last_done']
    assert after['scalars']['dt_last_done'] == expected_dt
    assert after['absolute_state'] == before['absolute_state'] and after['adaptation'] == before['adaptation']
    assert report['buffers_zeroed'] == list(MASKS[variant]['buffers'])


def test_full_mask_reproduces_fresh_restart_exactly():
    """Completeness of MA on a small system: same steps as a fresh physical restart."""
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    native = _native()
    fresh = NativeIAS15(native.absolute_state(), GMS, [], epsilon=1e-9, t0=float(native.sim.t),
                        initial_dt=float(native.sim.dt))
    apply_mask(native, 'MA')
    for _ in range(40):
        native.step(1e6)
        fresh.step(1e6)
        assert native.sim.t == fresh.sim.t and native.sim.dt == fresh.sim.dt
        assert native.sim.dt_last_done == fresh.sim.dt_last_done
        assert np.array_equal(native.absolute_state(), fresh.absolute_state())


def test_unmasked_native_history_differs_from_fresh_somewhere():
    """Sanity: the experiment is not vacuous on the synthetic system."""
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    native = _native()
    fresh = NativeIAS15(native.absolute_state(), GMS, [], epsilon=1e-9, t0=float(native.sim.t),
                        initial_dt=float(native.sim.dt))
    differs = False
    for _ in range(40):
        native.step(1e6)
        fresh.step(1e6)
        differs |= (native.sim.dt_last_done != fresh.sim.dt_last_done
                    or not np.array_equal(native.absolute_state(), fresh.absolute_state()))
    assert differs


def test_integrate_window_records_states_and_dense_grid():
    from submoon_research.diagnostics.ias15_frame_controls import common_grid
    native = _native(steps=0, t0=100.)
    result = integrate_window(native, IDENTITY, common_grid(100., 100.5, after=.5), max_steps=200)
    assert result['status'] == 'target_reached' and result['failure'] is None
    assert len(result['samples']) == 65 and result['samples'][-1]['elapsed'] == 1.
    assert all(r['absolute_state'] and r['coefficients_sha256'] for r in result['records'])
    assert result['scientific_admission'] is False


def test_bounds_before_integration():
    with pytest.raises(ValueError, match='60 s'):
        integrate_window(None, {}, {}, wall_seconds=61)
    with pytest.raises(ValueError, match='10000'):
        integrate_window(None, {}, {}, max_steps=10001)


def _rec(i, failure=None):
    return dict(left=float(i), right=float(i+1), dt_done=1., next_dt=1., absolute_state=[[float(i)]],
                coefficients_sha256='c'+str(i), elapsed_left=float(i), proposed_dt=1., max_step=2.,
                limiting_body=4, candidate_dt_per_body=[1.], failure=failure)


def test_verify_m0_exact_and_mismatch():
    fail = dict(reason='no_time_advance', error='e', left=2., right=2., dt_done=1e-6, next_dt=2e-6)
    reference = dict(records=[{k: _rec(i)[k] for k in ('left', 'right', 'dt_done', 'next_dt',
                                                      'absolute_state', 'coefficients_sha256')}
                              for i in range(2)], failure=fail)
    result = dict(status='guard', failure=dict(fail, elapsed_left=2.), records=[_rec(0), _rec(1), _rec(2, fail)])
    assert verify_m0(result, reference, expected_steps=2)['passed']
    result['records'][1]['dt_done'] = 1.5
    verdict = verify_m0(result, reference, expected_steps=2)
    assert not verdict['passed'] and verdict['first_mismatch_step'] == 1
    assert not verify_m0(result, reference, expected_steps=416)['passed']


def test_verify_ma_and_divergence():
    sample = dict(index=0, elapsed=0., relative_state=[[0.]])
    p00 = dict(status='target_reached', records=[_rec(0), _rec(1)], samples=[sample])
    ma = dict(status='target_reached', records=[_rec(0), _rec(1)], samples=[dict(sample)])
    assert verify_ma(ma, p00)['passed'] and first_divergence(ma, p00) is None
    ma['records'][1]['next_dt'] = 3.
    assert not verify_ma(ma, p00)['passed'] and first_divergence(ma, p00)['index'] == 1
