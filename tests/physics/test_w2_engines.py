"""Независимые ограничения новых сил/dense, события и недопустимые контракты."""
import json
import math
import multiprocessing as mp
from pathlib import Path

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from submoon_research.dynamics.fast_forces import (
    accelerations, figure_array, rhs, energy, j2_vector,
)
from submoon_research.dynamics.nominal import relative_rhs, massive_accelerations, massive_energy
from submoon_research.dynamics.native_ias15 import NativeIAS15, MassiveCache
from submoon_research.dynamics.dense_segments import PowerSegment
from submoon_research.dynamics.engine_compare import integrate_engine, NumericMode, ENGINES
from submoon_research.dynamics.kepler_reference import pericenter_reference
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.events.escape import EscapeTracker, radius_crossings


def binary():
    return np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1, 0]])


def require_native():
    pytest.importorskip('reboundx')


def test_compiled_forces_against_independent_vectorized_and_potential():
    gms = np.array([1., 120., 0.5])
    state = np.array([[0., 0, 0, 0, 0, 0], [17, 5, 9, 0.2, 0.3, 0.1],
                      [-30, 13, 8, 0.1, -0.2, 0.1], [1.1, 0.2, 0.3, 0, 0.9, 0.2]])
    figures = [dict(index=1, j2=0.01, radius=2., pole=[0.36, 0.48, 0.8])]
    fs = figure_array(figures, 3)
    np.testing.assert_allclose(rhs(state.ravel(), gms, fs),
                               relative_rhs(state.ravel(), gms, figures), atol=1e-14, rtol=1e-13)
    np.testing.assert_allclose(energy(state.ravel(), gms, fs),
                               massive_energy(state.ravel(), gms, figures), rtol=2e-14)
    acc = accelerations(state[:3, :3], gms, fs)
    np.testing.assert_allclose(acc, massive_accelerations(state[:3, :3], gms, figures), atol=1e-14)
    assert np.linalg.norm(np.sum(gms[:, None]*acc, axis=0)) < 1e-13
    pole = np.array(figures[0]['pole'])
    r = np.array([2.7, -1.3, 3.8])

    def potential(x):
        d = np.linalg.norm(x)
        return 120*0.01*4*(3*(np.dot(x, pole)/d)**2-1)/(2*d**3)

    def finite_difference(step):
        return np.array([(potential(r+np.eye(3)[i]*step)-potential(r-np.eye(3)[i]*step))/(2*step)
                         for i in range(3)])

    # Центральная разность O(h^2): при h=1e-4 её погрешность ~4.3e-8 и
    # маскирует аналитику. Экстраполяция Ричардсона даёт O(h^4) и позволяет
    # проверять j2_vector, не ослабляя допуск.
    h = 1e-3
    grad = (4*finite_difference(h/2) - finite_difference(h))/3
    np.testing.assert_allclose(j2_vector(r, 120., 0.01, 2., pole), -grad, rtol=1e-10)


def test_degree_nine_hidden_contact_and_crossings():
    # Два скрытых пересечения: концы снаружи. Член u^9 отличает от старого degree=7.
    c = np.zeros((10, 12))
    c[0, 6] = 2.
    c[1, 6] = -8.
    c[2, 6] = 8.
    c[9, 6] = 0.01
    c[0, 9] = -8.
    c[1, 9] = 16.
    c[8, 9] = 0.09
    dense = PowerSegment(0., 1., c)
    event = polynomial_contact(dense, 0, 1, 1, [0.5])
    assert event is not None and 0 < event['time'] < 0.5
    assert abs(dense(event['time'])[6]-0.5) < 1e-6
    roots = radius_crossings(dense, 0, 1, 0.5)
    assert [x['direction'] for x in roots] == [-1, 1]
    with pytest.raises(ValueError, match='Экстраполяция'):
        dense(1.1)


def test_tangency_and_other_body_contact():
    c = np.zeros((10, 18))
    c[0, 6] = 4.
    c[0, 12] = 5.25
    c[1, 12], c[2, 12] = -1., 1.
    event = polynomial_contact(PowerSegment(0, 1, c), 0, 1, 2, [0.1, 1.])
    assert event['body_index'] == 1
    assert abs(event['time']-0.5) < 1e-5


def test_return_exactly_at_escape_window_cancels_departure():
    c = np.zeros((10, 12))
    c[0, 6], c[1, 6], c[0, 9] = 3., -2., -2.
    tracker = EscapeTracker(1., 1., pending_since=0., outside=True, last_time=0.25)
    assert tracker.advance(PowerSegment(0, 1, c), 0.25, 1.) is None
    assert tracker.pending_since is None and tracker.returns == 1


def test_ias15_dense_endpoints_and_internal_points():
    require_native()
    start = binary()
    native = NativeIAS15(start, np.array([1.]), [], initial_dt=0.03)
    dense = native.step(0.03)
    times = np.linspace(0, dense.right, 23)
    reference, _ = pericenter_reference(start[-1], times)
    actual = dense(times)[-6:].T
    np.testing.assert_allclose(actual, reference, rtol=1e-11, atol=1e-12)
    assert np.max(abs(dense(0)-start.ravel())) < 1e-14
    # Независимый DOP853 по тем же физическим уравнениям на внутренних узлах.
    sol = solve_ivp(lambda t, y: np.r_[y[3:], -y[:3]/np.linalg.norm(y[:3])**3],
                    (0, times[-1]), start[-1], t_eval=times, method='DOP853',
                    rtol=1e-13, atol=1e-15)
    np.testing.assert_allclose(actual, sol.y.T, rtol=1e-11, atol=1e-12)


@pytest.mark.parametrize('engine', ENGINES)
def test_engine_circular_and_restart_contract(engine):
    if engine != 'dop853_jit':
        require_native()
    initial = binary()
    args = dict(period=2*math.pi, mode=NumericMode.tight(), times=np.linspace(0, 2, 21))
    full = integrate_engine(engine, initial, [1.], [], [0.01], 2., **args)
    assert full['run_status'] == 'completed', full['error']
    reference, _ = pericenter_reference(initial[-1], np.array([2.]))
    np.testing.assert_allclose(np.array(full['final_state'])[-6:], reference[0], atol=1e-8)
    saved = []

    def progress(value):
        if value['time'] > 0.8:
            saved.append(json.loads(json.dumps(value)))
            raise TimeoutError('forced checkpoint interruption')

    partial = integrate_engine(engine, initial, [1.], [], [0.01], 2., progress=progress, **args)
    assert partial['run_status'] == 'partial' and saved
    resumed = integrate_engine(engine, initial, [1.], [], [0.01], 2., resume=saved[0], **args)
    assert resumed['run_status'] == 'completed', resumed['error']
    np.testing.assert_allclose(resumed['final_state'], full['final_state'], atol=1e-8)
    with pytest.raises(ValueError, match='контракт'):
        integrate_engine(engine, initial, [1.], [], [0.01], 2.,
                         resume=saved[0] | {'contract_sha256': 'wrong'}, **args)


def test_cache_identity_and_forbidden_extrapolation():
    require_native()
    initial = np.array([[0., 0, 0, 0, 0, 0], [10., 0, 0, 0, 0.4, 0], [1., 0, 0, 0, 1, 0]])
    c = MassiveCache(initial, [1., 0.1], [], max_step=0.05)
    c.extend(0.2, lambda: None)
    np.testing.assert_allclose(c(0), initial[:-1].ravel())
    with pytest.raises(ValueError):
        c(0.3)
    bad = MassiveCache(initial, [1., 0.2], [])
    with pytest.raises(ValueError, match='Кеш'):
        integrate_engine('hierarchical_cached', initial, [1., 0.1], [], [0.01, 0.01],
                         0.2, period=6.28, cache=bad)


def test_native_rotated_j2_against_independent_force():
    require_native()
    initial = np.array([[0., 0, 0, 0, 0, 0], [5., 0, 2, 0, 0.2, 0], [1., 0.3, 0.1, 0, 1, 0]])
    gm = np.array([1., 3.])
    figs = [dict(index=1, j2=0.05, radius=0.3, pole=[0.36, 0.48, 0.8])]
    native = NativeIAS15(initial, gm, figs, initial_dt=1e-4)
    segment = native.step(1e-4)
    # u^1 коэффициент скорости / h — независимая нативная сила начала шага.
    got = segment.coefficients[1].reshape(-1, 6)[:, 3:]/(segment.right-segment.left)
    expected = relative_rhs(initial.ravel(), gm, figs).reshape(-1, 6)[:, 3:]
    np.testing.assert_allclose(got, expected, rtol=1e-11, atol=1e-13)


def restart_child(engine, initial, checkpoint_path, output_path):
    checkpoint = json.loads(Path(checkpoint_path).read_text())
    result = integrate_engine(engine, initial, [1.], [], [.01], 2., period=2*math.pi,
        mode=NumericMode.tight(), hill_km=10., escape_window=3., resume=checkpoint)
    Path(output_path).write_text(json.dumps(result))


@pytest.mark.parametrize('engine', ENGINES)
def test_separate_process_restart_with_escape_history(engine, tmp_path):
    require_native()
    initial = binary()
    checkpoint_path = tmp_path/'checkpoint.json'

    def save_and_interrupt(value):
        if value['time'] > .8:
            checkpoint_path.write_text(json.dumps(value))
            raise TimeoutError('Тестовое прерывание')

    partial = integrate_engine(engine, initial, [1.], [], [.01], 2., period=2*math.pi,
        mode=NumericMode.tight(), hill_km=10., escape_window=3., progress=save_and_interrupt)
    assert partial['run_status'] == 'partial'
    output = tmp_path/'resumed.json'
    child = mp.get_context('spawn').Process(target=restart_child,
        args=(engine, initial, str(checkpoint_path), str(output)))
    child.start()
    child.join(60)
    if child.is_alive():
        child.kill()
        child.join()
        pytest.fail('restart child timeout')
    assert child.exitcode == 0
    result = json.loads(output.read_text())
    assert result['run_status'] == 'completed'
    expected, _ = pericenter_reference(initial[-1], [2.])
    np.testing.assert_allclose(result['final_state'][-6:], expected[0], atol=1e-8)


def test_saved_cache_sha_and_continuation(tmp_path):
    require_native()
    from submoon_research.workflows.smoke import sha256
    initial = binary()
    cache = MassiveCache(initial, [1.], [])
    cache.extend(.1, lambda: None)
    path = tmp_path/'cache.npz'
    cache.save(path)
    loaded = MassiveCache(initial, [1.], [])
    loaded.load(path, sha256(path))
    loaded.extend(.2, lambda: None)
    np.testing.assert_allclose(loaded(.2), np.zeros(6), atol=1e-15)
    with pytest.raises(ValueError, match='SHA'):
        loaded.load(path, 'wrong')
