import numpy as np
import pytest
from scipy.integrate import solve_ivp

from submoon_research.dynamics.nominal import massive_accelerations, relative_rhs, massive_energy
from submoon_research.dynamics.baseline import integrate
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.dynamics.kepler_reference import pericenter_seed, pericenter_reference


def test_relative_nbody_equals_independent_inertial_with_j2():
    gms = np.array([0.01, 1.0, 0.1])
    positions = np.array([[0.0, 0.0, 0.0], [5.0, 1.0, 0.7], [-2.0, 4.0, -1.0]])
    figures = [dict(index=1, j2=0.01, radius=0.5, pole=[0.0, 0.0, 1.0])]
    y = np.zeros((4, 6))
    y[:3, :3] = positions
    y[-1, :3] = [0.2, -0.1, 0.07]
    result = relative_rhs(y.ravel(), gms, figures).reshape(4, 6)
    massive = massive_accelerations(positions, gms, figures)
    probe = np.zeros(3)
    for position, gm in zip(positions, gms):
        displacement = position - y[-1, :3]
        probe += gm * displacement / np.linalg.norm(displacement) ** 3
    r = y[-1, :3] - positions[1]
    radius = np.linalg.norm(r)
    z = r[2]
    probe += (
        1.5
        * gms[1]
        * 0.01
        * 0.5**2
        / radius**5
        * np.array(
            [
                (5 * z * z / radius**2 - 1) * r[0],
                (5 * z * z / radius**2 - 1) * r[1],
                (5 * z * z / radius**2 - 3) * z,
            ]
        )
    )
    np.testing.assert_allclose(result[-1, 3:], probe - massive[0], rtol=1e-13, atol=1e-14)
    np.testing.assert_allclose(np.sum(gms[:, None] * massive, axis=0), 0, atol=1e-16)
    np.testing.assert_allclose(result[1:-1, 3:], massive[1:] - massive[0], atol=1e-16)


def test_j2_pair_energy_gradient_and_reaction():
    gms = np.array([0.2, 1.0])
    y = np.array([[0.0, 0.0, 0.0, 0.0, 0.0, 0.0], [2.0, 1.0, 0.5, 0.0, 0.0, 0.0]])
    figures = [dict(index=1, j2=0.02, radius=0.4, pole=[0.0, 0.0, 1.0])]
    acceleration = massive_accelerations(y[:, :3], gms, figures)
    for body in range(2):
        gradient = []
        for axis in range(3):
            left = y.copy()
            right = y.copy()
            left[body, axis] -= 1e-5
            right[body, axis] += 1e-5
            gradient.append(
                (massive_energy(right, gms, figures) - massive_energy(left, gms, figures)) / 2e-5
            )
        np.testing.assert_allclose(
            acceleration[body], -np.array(gradient) / gms[body], rtol=1e-8, atol=1e-10
        )


def test_dense_contact_multiple_hidden_passages_and_tangent():
    def dense(t):
        t = np.asarray(t)
        result = np.zeros((12,) + t.shape)
        result[6] = 16 * (t - 0.25) * (t - 0.75)
        return result

    event = polynomial_contact(dense, 0.0, 1.0, 1, [0.1])
    assert event and event["time"] < 0.25 and event["body_index"] == 0

    def tangent(t):
        t = np.asarray(t)
        result = np.zeros((12,) + t.shape)
        result[6] = 0.1 + (t - 0.37) ** 2
        return result

    event = polynomial_contact(tangent, 0.0, 1.0, 1, [0.1])
    assert event and event["time"] == pytest.approx(0.37, abs=1e-5)
    assert polynomial_contact(tangent, 0.0, 1.0, 1, [0.09]) is None


def test_checkpoint_resumes_same_state_and_contact_classification():
    y = np.zeros((2, 6))
    y[1] = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    options = dict(rtol=1e-12, atol_position=1e-14, atol_velocity=1e-14, max_step=0.1)
    uninterrupted = integrate(y, [1.0], [], [0.1], 2 * np.pi, **options)
    segmented = integrate(y, [1.0], [], [0.1], 2 * np.pi, checkpoint_time=np.pi, **options)
    checkpoint = segmented["checkpoint"]
    resumed = integrate(
        checkpoint["state"], [1.0], [], [0.1], 2 * np.pi, t0=checkpoint["time"], **options
    )
    np.testing.assert_array_equal(resumed["final_state"], segmented["final_state"])
    np.testing.assert_allclose(
        resumed["final_state"], uninterrupted["final_state"], rtol=0, atol=1e-10
    )
    assert resumed["physical_outcome"] == uninterrupted["physical_outcome"] == "survived"
    y[1] = [1.0, 0.0, 0.0, -0.3, 0.2, 0.0]
    contact = integrate(y, [1.0], [], [0.5], 3.0, **options)
    assert contact["physical_outcome"] == "host_contact"
    assert abs(np.linalg.norm(np.array(contact["final_state"])[-6:-3]) - 0.5) < 1e-6


def test_rounded_pericenter_kepler_reference_closes_initial_conditions():
    initial = pericenter_seed(0.99)
    reference, metadata = pericenter_reference(initial, [0.0, float(2 * np.pi)], digits=60)
    np.testing.assert_array_equal(reference[0], initial)
    assert metadata["kepler_residual"] < 1e-40
    independent = solve_ivp(
        lambda t, y: np.r_[y[3:], -y[:3] / np.linalg.norm(y[:3]) ** 3],
        (0.0, 0.001),
        initial,
        method="DOP853",
        rtol=1e-13,
        atol=1e-16,
    )
    exact, _ = pericenter_reference(initial, [0.001], digits=60)
    np.testing.assert_allclose(independent.y[:, -1], exact[0], rtol=0, atol=1e-9)


def test_positive_two_body_energy_is_unresolved_not_permanent_escape():
    state = np.zeros((2, 6))
    state[1] = [1.0, 0.0, 0.0, 2.0, 0.0, 0.0]
    result = integrate(
        state,
        [1.0],
        [],
        [0.1],
        0.1,
        rtol=1e-12,
        atol_position=1e-14,
        atol_velocity=1e-14,
        max_step=0.01,
    )
    assert result["physical_outcome"] == "unresolved"
    assert result["permanent_escape_assessed"] is False
