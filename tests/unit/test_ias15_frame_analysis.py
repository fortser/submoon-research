"""Hash-guarded offline sampling of saved dense segments."""
import numpy as np
import pytest

from submoon_research.contracts.p2_files import digest
from submoon_research.diagnostics.ias15_frame_analysis import reference_samples
from submoon_research.dynamics.native_ias15 import NativeIAS15


def saved_segment():
    start = np.array([[10., 0., 0., .1, 0., 0.], [20., 0., 0., .2, 0., 0.]])
    a0, br = np.zeros((2, 3)), np.zeros((7, 2, 3))
    adapter = NativeIAS15.__new__(NativeIAS15)
    adapter.count = 2
    coeff = adapter._assemble(start, a0, br, 1.)
    end = coeff.sum(axis=0)
    coeff -= coeff[:, :1, :]
    record = dict(left=100., right=101., dt_done=1., absolute_state=end.tolist(),
                  coefficients_sha256=digest(coeff.reshape(10, -1).tolist()))
    raw = dict(left=100., right=101., dt_done=1., a0=a0.tolist(), br=br.tolist())
    return dict(absolute_state=start.tolist()), dict(records=[record]), dict(records=[raw]), dict(
        absolute=[100., 100.5, 101., 102.], elapsed=[0., .5, 1., 2.])


def test_exact_dense_interpolation_and_no_extrapolation():
    result = reference_samples(*saved_segment())
    assert result['verified_segments'] == 1
    assert len(result['samples']) == 3
    assert result['samples'][1]['relative_state'][1][0] == 10.05
    assert result['samples'][-1]['elapsed'] == 1.


def test_modified_buffers_fail_before_sampling():
    physical, replay, raw, grid = saved_segment()
    raw['records'][0]['a0'][1][0] = 1.
    with pytest.raises(ValueError, match='coefficient SHA'):
        reference_samples(physical, replay, raw, grid)
