"""W2-T008, этап F: прототип скомпилированного DOP853 против scipy-пути движка A.

Побитного совпадения нет (другой порядок операций); проверяется близость шага,
плотного вывода и полного пути в пределах округления, совпадение исходов и
событий, консервативность отсечки событий и restart.
"""
import math

import numpy as np
import pytest
from scipy.integrate import DOP853
from scipy.integrate._ivp.common import select_initial_step
from scipy.integrate._ivp.rk import Dop853DenseOutput

from submoon_research.dynamics import compiled_dop853 as cd
from submoon_research.dynamics import fast_forces
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine

MOON = np.array([[0., 0, 0, 0, 0, 0], [3., 0, 0, 0, math.sqrt(1.001/3), 0],
                 [1., 0, 0.1, 0, 1.05, 0.02]])


def _setup(initial, gms):
    mode = NumericMode()
    y = np.asarray(initial, dtype=float).ravel()
    gms = np.asarray(gms, dtype=float)
    fs = fast_forces.figure_array([], len(gms))
    atol = np.tile([mode.atol_position]*3+[mode.atol_velocity]*3, len(y)//6).astype(float)

    def fun(_, value):
        return fast_forces.rhs(np.asarray(value, dtype=float), gms, fs)
    return mode, y, gms, fs, atol, fun


def test_steps_and_dense_output_follow_scipy_dop853():
    mode, y, gms, fs, atol, fun = _setup(MOON, [1., 1e-3])
    period, bound = 2*math.pi, 20.0
    max_step = period*mode.step_fraction
    solver = DOP853(fun, 0.0, y.copy(), bound, rtol=mode.rtol, atol=atol, max_step=max_step)
    f = fun(0.0, y)
    h_abs = float(select_initial_step(fun, 0.0, y, bound, max_step, f, 1, 7, mode.rtol, atol))
    assert h_abs == solver.h_abs
    t, state = 0.0, y.copy()
    K, F, y_new = np.empty((16, y.size)), np.empty((7, y.size)), np.empty(y.size)
    point, acc = np.empty(y.size), np.empty((len(gms), 3))
    for _ in range(25):
        solver.step()
        reference = solver.dense_output()
        ok, t_new, h_next, h, nfev = cd._step(t, state, f, h_abs, bound, max_step, mode.rtol,
                                              atol, gms, fs, K, y_new, point, acc)
        assert ok and nfev >= 12
        np.testing.assert_allclose(t_new, solver.t, rtol=1e-13)
        np.testing.assert_allclose(y_new, solver.y, rtol=1e-11, atol=1e-13)
        np.testing.assert_allclose(h_next, solver.h_abs, rtol=1e-4)  # оценка ошибки — разность близких величин
        cd._dense(K, F, state, y_new, h, gms, fs, point, acc)
        np.testing.assert_allclose(F, reference.F, rtol=1e-8, atol=1e-12)  # старшие члены малы и с сокращением
        out = np.empty(y.size)
        for x in (0.0, 0.3, 0.77, 1.0):
            cd._dense_eval(F, state, x, out)
            np.testing.assert_allclose(out, reference(t + x*h), rtol=1e-11, atol=1e-13)
        # Продолжить из состояния scipy, чтобы сравнение шагов оставалось строгим.
        t, state, f, h_abs = solver.t, solver.y.copy(), solver.f.copy(), solver.h_abs


def _figures(rng, count):
    figures = []
    for index in range(min(count, 2)):
        pole = rng.normal(size=3)
        figures.append(dict(index=index, j2=float(rng.uniform(1e-4, 2e-2)),
                            radius=float(rng.uniform(0.05, 0.3)), pole=list(pole/np.linalg.norm(pole))))
    return figures


@pytest.mark.parametrize('seed', range(3))
def test_scalar_force_and_energy_kernels_match_fast_forces(seed):
    rng = np.random.default_rng(400+seed)
    for _ in range(50):
        count = int(rng.integers(1, 5))
        gms = rng.uniform(1e-4, 1.0, count)
        gms[0] = 1.0
        state = rng.normal(size=6*(count+1))*2.0
        state[:6] = 0.0
        fs = fast_forces.figure_array(_figures(rng, count), count)
        out, acc = np.empty(state.size), np.empty((count, 3))
        cd.rhs_into(state, gms, fs, out, acc)
        np.testing.assert_allclose(out, fast_forces.rhs(state, gms, fs), rtol=1e-12, atol=1e-14)
        np.testing.assert_allclose(cd.energy_scalar(state, gms, fs),
                                   fast_forces.energy(state, gms, fs), rtol=1e-12, atol=1e-14)


def _exact_events(dense, radii, hill_in, hill_out):
    """Найдёт ли прежний точный код событие на этом шаге (или сбой — тогда тоже нужен Python)."""
    from submoon_research.events.dense_contact import polynomial_contact
    from submoon_research.events.escape import radius_crossings
    try:
        contact = polynomial_contact(dense, 0.0, 1.0, len(radii), radii) is not None
    except (ValueError, ArithmeticError):
        contact = True
    try:
        crossing = any(radius_crossings(dense, 0.0, 1.0, r) for r in (hill_in, hill_out))
    except (ValueError, ArithmeticError):
        crossing = True
    return contact, crossing


def _scratch(n):
    size = 2*cd.SCREEN_DEPTH + 2
    return (np.empty((8, n)), np.empty((8, 3)), np.empty(size), np.empty(size),
            np.empty(size, dtype=np.int64))


@pytest.mark.parametrize('seed', range(3))
def test_compiled_screen_never_skips_an_exact_event(seed):
    rng = np.random.default_rng(300+seed)
    flagged = skipped = events = 0
    for _ in range(300):
        bodies = int(rng.integers(1, 4))
        scale = float(10**rng.uniform(0, 4))
        y_old = rng.normal(size=6*(bodies+1))*scale
        y_old[:6] = 0.0
        F = rng.normal(size=(7, y_old.size))*scale*float(rng.uniform(0.01, 1.0))
        F[:, :6] = 0.0
        radii = rng.uniform(0.05, 1.5, bodies)*scale
        hill_in = float(rng.uniform(0.3, 2.0))*scale
        dense = Dop853DenseOutput(0.0, 1.0, y_old, F)
        contact, crossing = _exact_events(dense, radii, hill_in, 2*hill_in)
        flags = cd._screen(F, y_old, radii, 1e-7, hill_in, 2*hill_in, *_scratch(y_old.size))
        if contact:
            assert flags & cd.FLAG_CONTACT
        if crossing:
            assert flags & cd.FLAG_CROSSING
        events += contact or crossing
        flagged += flags != 0
        skipped += flags == 0
    assert events > 20 and skipped > 30


def test_subdivision_clears_a_near_pericenter_pass():
    # Проба проходит у хозяина на 1.02 радиуса: оценка по всему шагу не исключает
    # контакт, деление шага доказывает его отсутствие без Python-проверки.
    radius = 1.0
    peri = 1.02*radius
    speed = 1.2
    # Полином степени 7 по x∈[0,1], проходящий через прямолинейный пролёт (точное представление).
    y_old = np.array([0, 0, 0, 0, 0, 0, peri, -0.5*speed, 0, 0, speed, 0], dtype=float)
    F = np.zeros((7, 12))
    F[0, 7] = speed           # delta_y по координате y
    F[1, 7] = 0.0             # h*f_old - delta (скорость постоянна, h=1)
    F[2, 7] = 0.0
    F[0, 10] = 0.0
    dense = Dop853DenseOutput(0.0, 1.0, y_old, F)
    np.testing.assert_allclose(dense(0.5)[6:9], [peri, 0.0, 0.0], atol=1e-12)
    S, coeff, lo, hi, depth = _scratch(12)
    center, envelope = cd._bounds(F, y_old, 0.0, 1.0, 6, 0, S, coeff)
    assert center - envelope <= radius + 1e-7          # грубая оценка не исключает контакт
    assert cd._screen(F, y_old, np.array([radius]), 1e-7, -1.0, -1.0, S, coeff, lo, hi, depth) == 0
    assert _exact_events(dense, np.array([radius]), 100.0, 200.0) == (False, False)


def _engine_cases():
    circular = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1, 0]])
    escaping = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1.6, 0.1]])
    excursion = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1.25, 0.05]])
    infall = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 0.3, 0]])
    return [
        ('circular', circular, [1.], [0.01], 12., None, None),
        ('escape', escaping, [1.], [0.01], 40., 3., 2.),
        ('excursion', excursion, [1.], [0.01], 60., 2., 30.),
        ('host_contact', infall, [1.], [0.5], 20., 5., 5.),
        ('moon', MOON, [1., 1e-3], [0.01, 0.02], 60., 8., 10.),
    ]


@pytest.mark.parametrize('case', _engine_cases(), ids=lambda c: c[0])
def test_full_path_matches_scipy_engine_a(case):
    name, initial, gms, radii, horizon, hill, window = case
    kwargs = dict(period=2*math.pi, mode=NumericMode(), hill_km=hill, escape_window=window,
                  times=np.linspace(0, horizon, 33))
    ref = integrate_engine('dop853_jit', initial, gms, [], radii, horizon, **kwargs)
    new = cd.integrate_compiled(initial, gms, [], radii, horizon, batch_steps=64, **kwargs)
    assert ref['run_status'] == new['run_status'] == 'completed', (ref['error'], new['error'])
    assert new['physical_outcome'] == ref['physical_outcome']
    assert (new['event'] is None) == (ref['event'] is None)
    if ref['event']:
        assert new['event']['event'] == ref['event']['event']
        assert new['event'].get('body_index') == ref['event'].get('body_index')
        assert abs(new['event']['time']-ref['event']['time']) < 1e-8*2*math.pi
    assert abs(new['last_valid_time']-ref['last_valid_time']) < 1e-8*2*math.pi
    np.testing.assert_allclose(new['final_state'], ref['final_state'], rtol=1e-7, atol=1e-7)
    assert abs(new['steps']-ref['steps']) <= max(2, 0.01*ref['steps'])
    assert len(new['samples']) == len(ref['samples'])
    for a, b in zip(new['samples'], ref['samples']):
        assert a['time'] == b['time']
        np.testing.assert_allclose(a['state'], b['state'], rtol=1e-7, atol=1e-7)
    assert new['massive_energy_relative_drift'] < 1e-9
    if ref['checkpoint']['escape']:
        for key in ('outside', 'temporary_exits', 'returns'):
            assert new['checkpoint']['escape'][key] == ref['checkpoint']['escape'][key]


def test_partial_and_resume_continue_to_the_same_result():
    kwargs = dict(period=2*math.pi, mode=NumericMode(), hill_km=8., escape_window=10.,
                  times=np.linspace(0, 40., 17))
    full = cd.integrate_compiled(MOON, [1., 1e-3], [], [0.01, 0.02], 40., batch_steps=50, **kwargs)
    calls = []

    def budget():
        calls.append(1)
        if len(calls) > 4:
            raise TimeoutError('forced interruption')

    partial = cd.integrate_compiled(MOON, [1., 1e-3], [], [0.01, 0.02], 40., batch_steps=50,
                                    budget=budget, **kwargs)
    assert partial['run_status'] == 'partial' and 0 < partial['last_valid_time'] < 40.
    resumed = cd.integrate_compiled(MOON, [1., 1e-3], [], [0.01, 0.02], 40., batch_steps=50,
                                    resume=partial['checkpoint'], **kwargs)
    assert resumed['run_status'] == 'completed', resumed['error']
    np.testing.assert_allclose(resumed['final_state'], full['final_state'], rtol=1e-6, atol=1e-6)
    with pytest.raises(ValueError, match='контракт'):
        cd.integrate_compiled(MOON, [1., 1e-3], [], [0.01, 0.02], 40.,
                              resume=partial['checkpoint'] | {'contract_sha256': 'x'}, **kwargs)


@pytest.mark.parametrize('e0', [0.0, 0.5, 0.95])
def test_eccentricity_monitor_reports_osculating_e(e0):
    # Двухтельная орбита с перицентром r=1: скорость в перицентре sqrt((1+e)/1).
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, math.sqrt(1+e0), 0]])
    period = 2*math.pi*(1/(1-e0))**1.5
    out = cd.integrate_compiled(initial, [1.], [], [0.01], period, period=period, mode=NumericMode())
    monitor = out['eccentricity_monitor']
    assert out['run_status'] == 'completed'
    assert abs(monitor['max_bound']-e0) < 1e-6
    assert (monitor['first_bound_at_limit_time'] is not None) == (e0 >= 0.9)
    assert (monitor['bound_approach_steps_at_limit'] > 0) == (e0 >= 0.9)


def test_checkpoint_rejects_another_implementation_version():
    # Контрпример аудита W2-I011: смена версии реализации обязана менять контракт restart.
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1, 0]])
    saved = []

    def interrupt(checkpoint):
        saved.append(checkpoint)
        raise TimeoutError('interruption')

    cd.integrate_compiled(initial, [1.], [], [.01], 2., period=2*math.pi, batch_steps=1, progress=interrupt)
    assert saved and saved[0]['implementation_sha256'] == cd.implementation_fingerprint()
    version = cd.PROTOTYPE_VERSION
    cd.PROTOTYPE_VERSION = version + 1000
    try:
        with pytest.raises(ValueError, match='контракт'):
            cd.integrate_compiled(initial, [1.], [], [.01], 2., period=2*math.pi, resume=saved[0])
    finally:
        cd.PROTOTYPE_VERSION = version
    resumed = cd.integrate_compiled(initial, [1.], [], [.01], 2., period=2*math.pi, resume=saved[0])
    assert resumed['run_status'] == 'completed'


def test_eccentricity_monitor_stops_at_terminal_contact():
    # Контрпример аудита W2-I013: старт на контактной поверхности, e = 0.95 в апоцентре.
    e0 = 0.95
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, math.sqrt(1-e0), 0]])
    out = cd.integrate_compiled(initial, [1.], [], [1.0], 50., period=2*math.pi, mode=NumericMode())
    monitor = out['eccentricity_monitor']
    assert out['physical_outcome'] == 'host_contact'
    assert monitor['first_bound_at_limit_time'] is not None
    assert monitor['first_bound_at_limit_time'] <= out['last_valid_time']
    assert out['numerical_domain']['outside_validated_domain']


def test_eccentricity_history_survives_restart():
    e0 = 0.95
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, math.sqrt(1+e0), 0]])
    period = 2*math.pi*(1/(1-e0))**1.5
    saved = []

    def interrupt(checkpoint):
        if checkpoint['time'] > 0.1*period:
            saved.append(checkpoint)
            raise TimeoutError('interruption')

    partial = cd.integrate_compiled(initial, [1.], [], [0.01], period, period=period, batch_steps=20,
                                    progress=interrupt)
    assert partial['run_status'] == 'partial' and saved
    assert saved[0]['diagnostics']['eccentricity_monitor'][1] == 0.0
    resumed = cd.integrate_compiled(initial, [1.], [], [0.01], period, period=period, resume=saved[0])
    # Без восстановления истории первое превышение было бы временем restart, а не стартом.
    assert resumed['eccentricity_monitor']['first_bound_at_limit_time'] == 0.0
    assert resumed['eccentricity_monitor']['max_bound'] >= saved[0]['diagnostics']['eccentricity_monitor'][0]
