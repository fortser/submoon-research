"""Проверка независимого эталона T071, не допуск научных режимов."""
import mpmath as mp
import numpy as np

from submoon_research.diagnostics.physics_audit_v1 import (
    field_potential, independent_force, primary_inputs, load, MODEL,
)


def test_independent_monopole_and_indirect_term():
    state = np.zeros((3, 6))
    state[1, 0], state[2, 0] = 1., .25
    relative, absolute, potential = independent_force(state, [2., 3.], [])
    # Инерциально: a_host=3, a_partner=-2, a_probe=-2/.25^2+3/.75^2.
    np.testing.assert_allclose(absolute[:, 0], [3., -2.], rtol=0, atol=1e-14)
    np.testing.assert_allclose(relative[:, 0], [0., -5., -2/.25**2+3/.75**2-3], atol=1e-14)
    assert potential == -6.


def test_j2_potential_gradient_equator_and_pole():
    with mp.workdps(50):
        figure = dict(pole=[0., 0., 1.], j2=.03, radius=.8)
        mu, radius, j2, distance = map(mp.mpf, [2., .8, .03, 2.])
        equatorial = -mp.diff(lambda x: field_potential([x, 0, 0], mu, figure), distance)
        polar = -mp.diff(lambda z: field_potential([0, 0, z], mu, figure), distance)
        assert mp.almosteq(equatorial, -mp.mpf('1.5')*mu*j2*radius**2/distance**4)
        assert mp.almosteq(polar, 3*mu*j2*radius**2/distance**4)


def test_reference_translation_and_no_massless_backreaction():
    gm = np.array([2., 3.])
    state = np.array([[0., 0, 0, 0, 0, 0], [1., 1., 0., 0, 0, 0], [.2, .1, .3, 0, 0, 0]])
    figures = [dict(index=1, j2=.01, radius=.1, pole=[0., 0., 1.])]
    relative, absolute, potential = independent_force(state, gm, figures)
    translated = state.copy()
    translated[:, :3] += [4., -3., 2.]
    r2, a2, p2 = independent_force(translated, gm, figures)
    np.testing.assert_allclose(r2, relative, rtol=1e-13, atol=1e-14)
    np.testing.assert_allclose(a2, absolute, rtol=1e-13, atol=1e-14)
    assert p2 == potential
    state[-1, :3] *= 2
    _, a3, p3 = independent_force(state, gm, figures)
    np.testing.assert_array_equal(a3, absolute)
    assert p3 == potential


def test_primary_checks_have_strict_boolean_results():
    values = primary_inputs(load(MODEL))
    assert len(values) == 14 and all(type(v) is bool and v for v in values.values())
