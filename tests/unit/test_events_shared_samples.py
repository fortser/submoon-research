"""W2-T008, этап D: общая подготовка событий шага эквивалентна прежнему коду.

Сравнение с дословными копиями прежних модулей (tests/reference): контакт,
пересечения радиусов, история ухода и полный путь движка A должны давать те же
результаты (побитно) или те же исключения.
"""
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from submoon_research.dynamics.dense_segments import PowerSegment, chebyshev_samples
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.events.escape import EscapeTracker, ProbeRadiusPolynomial, radius_crossings

_spec = importlib.util.spec_from_file_location(
    'w2t008_reference_loader', Path(__file__).resolve().parents[1]/'reference'/'loader.py')
_loader = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_loader)
REF_ENGINE, REF_CONTACT, REF_ESCAPE = _loader.load_reference()


def outcome(fn, *args, **kwargs):
    try:
        return ('ok', fn(*args, **kwargs))
    except (ValueError, ArithmeticError) as exc:
        return (type(exc).__name__, str(exc))


def random_segment(rng, bodies, scale, t0=0.0, t1=1.0, wobble=1.0):
    """Полином степени 9: хозяин в начале координат, остальные тела и проба движутся."""
    coeff = np.zeros((10, 6*(bodies+1)))
    for body in range(1, bodies+1):
        base = rng.normal(size=6)*scale
        coeff[0, 6*body:6*body+6] = base
        decay = wobble*scale/np.arange(1, 10)[:, None]**2
        coeff[1:, 6*body:6*body+6] = rng.normal(size=(9, 6))*decay
    return PowerSegment(t0, t1, coeff)


@pytest.mark.parametrize('seed', range(4))
def test_contact_matches_reference_with_and_without_shared_samples(seed):
    rng = np.random.default_rng(seed)
    hits = 0
    for trial in range(250):
        bodies = int(rng.integers(1, 4))
        scale = float(10**rng.uniform(-1, 4))
        dense = random_segment(rng, bodies, scale, wobble=float(rng.uniform(0.05, 1.5)))
        radii = list(rng.uniform(0.05, 1.2, bodies)*scale)
        ref = outcome(REF_CONTACT.polynomial_contact, dense, 0.0, 1.0, bodies, radii)
        new = outcome(polynomial_contact, dense, 0.0, 1.0, bodies, radii)
        shared = outcome(polynomial_contact, dense, 0.0, 1.0, bodies, radii,
                         samples=chebyshev_samples(dense, 0.0, 1.0))
        assert new == ref, (seed, trial)
        assert shared == ref, (seed, trial)
        hits += ref[0] == 'ok' and ref[1] is not None
    assert hits > 10  # набор действительно содержит контакты, а не только промахи


@pytest.mark.parametrize('seed', range(4))
def test_crossings_match_reference_for_all_radii(seed):
    rng = np.random.default_rng(100+seed)
    crossings = 0
    for trial in range(200):
        scale = float(10**rng.uniform(-1, 4))
        dense = random_segment(rng, 1, scale, wobble=float(rng.uniform(0.05, 1.5)))
        prepared = ProbeRadiusPolynomial(dense, 0.0, 1.0)
        for radius in rng.uniform(0.2, 2.5, 4)*scale:
            ref = outcome(REF_ESCAPE.radius_crossings, dense, 0.0, 1.0, float(radius))
            assert outcome(radius_crossings, dense, 0.0, 1.0, float(radius)) == ref
            assert outcome(radius_crossings, dense, 0.0, 1.0, float(radius), prepared=prepared) == ref
            crossings += ref[0] == 'ok' and len(ref[1]) > 0
    assert crossings > 20


@pytest.mark.parametrize('seed', range(3))
def test_escape_history_matches_reference_step_by_step(seed):
    rng = np.random.default_rng(200+seed)
    hill = 10.0
    for path in range(20):
        ref = REF_ESCAPE.EscapeTracker(hill, float(rng.uniform(0.5, 4.0)))
        new = EscapeTracker(ref.hill_km, ref.window_seconds)
        shared = EscapeTracker(ref.hill_km, ref.window_seconds)
        t = 0.0
        for step in range(30):
            t1 = t+float(rng.uniform(0.1, 1.0))
            dense = random_segment(rng, 1, float(rng.uniform(0.5, 2.5))*hill, t, t1,
                                   wobble=float(rng.uniform(0.05, 0.6)))
            a = outcome(ref.advance, dense, t, t1)
            b = outcome(new.advance, dense, t, t1)
            c = outcome(shared.advance, dense, t, t1, samples=chebyshev_samples(dense, t, t1))
            assert a == b == c, (seed, path, step)
            assert ref.snapshot() == new.snapshot() == shared.snapshot()
            if a[0] != 'ok' or a[1] is not None:
                break
            t = t1


def _engine_cases():
    circular = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1, 0]])
    escaping = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1.6, 0.1]])
    excursion = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1.25, 0.05]])
    infall = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 0.3, 0]])
    moon = np.array([[0., 0, 0, 0, 0, 0], [3., 0, 0, 0, math.sqrt(1.001/3), 0],
                     [1., 0, 0.1, 0, 1.05, 0]])
    return [
        ('circular', circular, [1.], [0.01], 2*math.pi, 12., None, None),
        ('escape', escaping, [1.], [0.01], 2*math.pi, 40., 3., 2.),
        ('excursion', excursion, [1.], [0.01], 2*math.pi, 60., 2., 30.),
        ('host_contact', infall, [1.], [0.5], 2*math.pi, 20., 5., 5.),
        ('moon', moon, [1., 1e-3], [0.01, 0.02], 2*math.pi, 60., 8., 10.),
    ]


@pytest.mark.parametrize('case', _engine_cases(), ids=lambda c: c[0])
def test_engine_a_full_path_bitwise_equal_to_reference(case):
    from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine
    name, initial, gms, radii, period, horizon, hill, window = case
    kwargs = dict(period=period, mode=NumericMode(), hill_km=hill, escape_window=window,
                  times=np.linspace(0, horizon, 33))
    ref = REF_ENGINE.integrate_engine('dop853_jit', initial, gms, [], radii, horizon, **kwargs)
    new = integrate_engine('dop853_jit', initial, gms, [], radii, horizon, **kwargs)
    for result in (ref, new):
        for key in ('wall_seconds', 'cpu_seconds'):
            result.pop(key)
    assert ref['run_status'] == 'completed', ref['error']
    assert json.dumps(new, sort_keys=True) == json.dumps(ref, sort_keys=True)
