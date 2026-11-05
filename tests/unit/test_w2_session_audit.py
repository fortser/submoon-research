"""Аналитические контроли арифметики аудита; не интеграционный допуск B."""
import numpy as np

from submoon_research.diagnostics.w2_session_audit import derivatives, j2_probe


STATE = np.array([[0., 0., 0., 0., 0., 0.], [1., 0., 0., 0., 1., 0.]])


def test_circular_point_mass_derivatives():
    a, jerk, snap, terms = derivatives(STATE, [1.])
    np.testing.assert_array_equal(a, [-1., 0., 0.])
    np.testing.assert_array_equal(jerk, [0., -1., 0.])
    np.testing.assert_array_equal(snap, [1., 0., 0.])
    np.testing.assert_array_equal(terms.sum(axis=0), a)


def test_derivatives_are_galilean_invariant():
    translated = STATE.copy()
    translated[:, :3] += [128., 256., -64.]
    translated[:, 3:] += [3., -2., 4.]
    for old, new in zip(derivatives(STATE, [1.]), derivatives(translated, [1.])):
        np.testing.assert_array_equal(old, new)


def test_j2_equatorial_and_polar_limits():
    figure = dict(index=0, j2=.01, radius=1., pole=[0., 0., 1.])
    np.testing.assert_allclose(j2_probe(STATE, [1.], [figure]), [-.015, 0., 0.], atol=1e-18)
    polar = STATE.copy()
    polar[1, :3] = [0., 0., 1.]
    np.testing.assert_allclose(j2_probe(polar, [1.], [figure]), [0., 0., .03], atol=1e-18)
