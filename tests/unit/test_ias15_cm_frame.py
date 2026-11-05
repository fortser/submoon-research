"""Synthetic W2-P014 step 1 checks; not results for the historical B orbit."""
import math

import numpy as np
import pytest

from submoon_research.diagnostics.ias15_cm_frame import (
    PRS23_FACTOR, VARIANTS, classify, cm_transform, min_unclipped_dt, physical_prs23_dt, run_cm,
)
from submoon_research.diagnostics.ias15_frame_controls import pair_differences

OFFSET_X, OFFSET_V = 1e6, 3.
BASE = np.array([[0., 0., 0., 0., 0., 0.],
                 [5., 0., 0., 0., math.sqrt(1./5.), 0.],
                 [0., 1., 0., -1.15, 0., 0.05]])
IDENTITY = dict(period_seconds=2*math.pi, gm_host=1., step_fraction=.025)


def physical():
    state = BASE.copy()
    state[:, :3] += OFFSET_X
    state[:, 3:] += OFFSET_V
    return dict(time=100., dt=1e-3, absolute_state=state.tolist(), gms=[1., 1e-3], figures=[],
                adaptation=dict(epsilon=1e-9, min_dt=0., adaptive_mode='PRS23', iterations_max_exceeded=0))


def test_unknown_variant_and_bad_masses():
    with pytest.raises(ValueError, match='Unknown'):
        cm_transform(physical(), 'P00')
    p = physical()
    p['gms'] = [1.]
    with pytest.raises(ValueError, match='massless'):
        cm_transform(p, 'G')


@pytest.mark.parametrize('variant', VARIANTS)
def test_transform_is_galilean_and_exact_in_positions(variant):
    p = physical()
    c = cm_transform(p, variant)
    old, new = np.array(p['absolute_state']), np.array(c['absolute_state'])
    assert c['pairwise_position_exact']
    assert np.array_equal(pair_differences(new[:, :3]), pair_differences(old[:, :3]))
    assert np.max(np.abs(pair_differences(new[:, 3:])-pair_differences(old[:, 3:]))) <= c['velocity_ulp_limit_km_s']
    assert np.allclose(c['cm_after'][3:], 0., atol=1e-12)
    if variant == 'G':
        assert np.allclose(c['cm_after'][:3], 0., atol=1e-6) and c['max_abs_coordinate_km'] < 10.
    else:
        assert np.array_equal(new[:, :3], old[:, :3]) and c['max_abs_coordinate_km'] > OFFSET_X
    assert p['absolute_state'][0][0] == OFFSET_X  # input untouched


def test_physical_prs23_circular_two_body():
    state = [[0., 0., 0., 0., 0., 0.], [1., 0., 0., 0., 1., 0.]]
    assert math.isclose(physical_prs23_dt(state, [1.], 1e-9), math.sqrt(2.)*PRS23_FACTOR(1e-9))


def _result(dts, target_reached=True):
    t, records = 0., []
    for dt in dts:
        records.append(dict(right=t+dt, dt_done=dt))
        t += dt
    return dict(records=records, target_time=t if target_reached else t+1., target_reached=target_reached,
                status='target_reached' if target_reached else 'guard')


def test_min_unclipped_and_classification():
    assert min_unclipped_dt(_result([10., 7., .001])) == 7.
    assert classify(_result([10., 6., .1]), _result([10., .4, .1])) == 'confirm'
    assert classify(_result([10., 6., .1]), _result([10., 2., .1], target_reached=False)) == 'confirm'
    assert classify(_result([10., 6., .1]), _result([10., 9., .1])) == 'coordinate_noise_rejected'
    assert classify(_result([10., 4., .1]), _result([10., .4, .1])) == 'hypothesis_rejected'
    assert classify(_result([10., 6.], target_reached=False), _result([10., .4])) == 'hypothesis_rejected'
    assert classify(_result([10., 6., .1]), _result([10., 3., .1])) == 'inconclusive'


@pytest.mark.parametrize('status', ['wall_budget', 'step_budget'])
def test_resource_limits_are_inconclusive(status):
    good = _result([10., 6., .1])
    unfinished = _result([10., 6.], target_reached=False)
    unfinished['status'] = status
    assert classify(good, unfinished) == 'inconclusive'
    assert classify(unfinished, good) == 'inconclusive'


def test_empty_results_are_inconclusive():
    empty = dict(records=[], target_time=10., target_reached=False, status='wall_budget')
    assert classify(empty, _result([10., .4, .1])) == 'inconclusive'
    assert classify(_result([10., 6., .1]), empty) == 'inconclusive'


def test_bounds_before_native_initialization():
    with pytest.raises(ValueError, match='60 s'):
        run_cm({}, {}, {}, wall_seconds=61)
    with pytest.raises(ValueError, match='10000'):
        run_cm({}, {}, {}, max_steps=10001)


def test_synthetic_g_and_gf_agree_in_relative_motion():
    pytest.importorskip('reboundx')
    from submoon_research.diagnostics.ias15_frame_controls import common_grid, compare_samples
    grid = common_grid(100., 100.5, after=.5)
    results = {v: run_cm(cm_transform(physical(), v), IDENTITY, grid, max_steps=500) for v in VARIANTS}
    for r in results.values():
        assert r['status'] == 'target_reached' and r['failure'] is None and len(r['samples']) == 65
    diff = compare_samples(results['G'], results['GF'], radius_scale=1., speed_scale=1.)
    assert diff['common_points'] == 65 and diff['max_normalized_position'] < 1e-8
