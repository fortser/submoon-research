"""Synthetic frame matrix checks; not results for the historical B orbit."""
import json
import math

import numpy as np
import pytest

from submoon_research.diagnostics.ias15_frame_controls import (
    VARIANTS, common_grid, compare_samples, pair_differences, run_physical, transform,
)
from test_p2_file_contracts import load


def physical():
    return dict(time=100., dt=.001, absolute_state=[[100., 200., 300., 0., 0., 0.],
                [110., 200., 300., 0., math.sqrt(.1), 0.]], gms=[1.], figures=[],
                adaptation=dict(epsilon=1e-9, min_dt=0., adaptive_mode='PRS23', iterations_max_exceeded=0))


@pytest.mark.parametrize('variant', VARIANTS)
def test_transform_exact_geometry_and_no_velocity_boost(variant):
    p = physical()
    result = transform(p, variant)
    assert np.array_equal(pair_differences(result['absolute_state']), pair_differences(p['absolute_state']))
    assert np.array_equal(np.array(result['absolute_state'])[:, 3:], np.array(p['absolute_state'])[:, 3:])
    assert result['time'] == (0. if variant[2] == '1' else 100.)
    assert result['absolute_state'][0][:3] == ([0., 0., 0.] if variant[1] == '1' else [100., 200., 300.])
    assert p['absolute_state'][0][:3] == [100., 200., 300.]


def test_bad_geometry_stops_before_integration():
    p = physical()
    p['absolute_state'] = [[1e16, 0., 0., 0., 0., 0.], [1., 0., 0., 0., 0., 0.], [2., 0., 0., 0., 0., 0.]]
    with pytest.raises(ValueError, match='pairwise'):
        transform(p, 'P10')


def test_common_grid_at_historical_time():
    t0, tf = 25525800914.631542, 25526330757.864594
    g = common_grid(t0, tf)
    assert len(g['elapsed']) == len(g['absolute']) == 66
    assert tf in g['absolute'] and g['absolute'][-1] == tf+1000.
    assert np.array_equal(np.asarray(g['elapsed'])+t0, g['absolute'])
    assert g['target_elapsed'] == 530843.2330513


def test_comparison_intersects_time_grid_not_step_index():
    a = dict(samples=[dict(index=i, elapsed=float(i), relative_state=[[0.]*6, [float(i)]*6]) for i in range(3)])
    b = dict(samples=[dict(index=1, elapsed=1., relative_state=[[0.]*6, [2.]*6])])
    result = compare_samples(a, b, radius_scale=10., speed_scale=2.)
    assert result['common_points'] == 1 and result['max_common_elapsed'] == 1.
    assert result['max_position_difference_km'] == math.sqrt(3.)
    assert result['max_normalized_speed'] == math.sqrt(3.)/2
    b['samples'][0]['elapsed'] = 2.
    with pytest.raises(ValueError, match='elapsed'):
        compare_samples(a, b, radius_scale=10., speed_scale=2.)


@pytest.mark.parametrize('variant', VARIANTS)
def test_synthetic_complete_dense_grid(variant):
    pytest.importorskip('reboundx')
    config = transform(physical(), variant)
    result = run_physical(config, dict(period_seconds=2*math.pi*math.sqrt(1000.), gm_host=1., step_fraction=.025),
                          common_grid(100., 100.5, after=.5), max_steps=100)
    assert result['status'] == 'target_reached' and result['failure'] is None
    assert result['last_valid_elapsed'] == 1.
    assert len(result['samples']) == 65  # historical failure point already on the regular synthetic grid
    assert result['samples'][-1]['kind'] == 'dense'
    assert result['samples'][-1]['elapsed'] == 1.
    assert result['scientific_admission'] is False


def test_native_baseline_mismatch_blocks_physical(tmp_path):
    cli = load('diag_ias15_frames')
    ref = dict(identity={'synthetic': True}, records=[{'x': 1.}], failure={'reason': 'synthetic'})
    native = dict(restart_kind='native', requested_steps=417, snapshot_sha256=cli.SNAPSHOT_SHA, **ref)
    (tmp_path/'N.json').write_text(json.dumps(native))
    assert all(cli.verify_baseline(tmp_path, ref).values())
    native['records'] = [{'x': 2.}]
    (tmp_path/'N.json').write_text(json.dumps(native))
    with pytest.raises(ValueError, match='baseline mismatch'):
        cli.verify_baseline(tmp_path, ref)


def test_bounds_before_native_initialization():
    with pytest.raises(ValueError, match='60 s'):
        run_physical({}, {}, {}, wall_seconds=61)
    with pytest.raises(ValueError, match='10000'):
        run_physical({}, {}, {}, max_steps=10001)
