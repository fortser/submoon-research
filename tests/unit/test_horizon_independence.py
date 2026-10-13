"""W2-C035: расчёт не зависит от горизонта задания (кроме укороченного последнего шага).

На этом держится покрытие ступенчатой схемы P2: пары, у которых на длинном горизонте общей
оказалась лишь стартовая точка, — те же вычисления, что на коротких ступенях с подробной сеткой.
Если изменение кода сделает шаги зависящими от горизонта, тест упадёт.
"""
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
YEAR = 365.25*86400.0


@pytest.fixture(scope='module')
def cases():
    from submoon_research.workflows.w2_comparison import load_cases
    _, found = load_cases(ROOT)
    return {c['orbit_id']: c for c in found}


def integrate(case, horizon, variant, times):
    """Те же параметры, что у стенда (admission_p2_horizons._job), но с явной сеткой моментов."""
    from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine
    from submoon_research.dynamics.compiled_dop853 import integrate_compiled
    kwargs = dict(period=case['period_seconds'], mode=NumericMode.tight() if variant == 'T' else NumericMode(),
                  hill_km=case.get('hill_km'), escape_window=case.get('escape_window'), times=times)
    args = (case['state'], case['gms'], case['figures'], case['radii'], horizon)
    if variant == 'B':
        return integrate_engine('ias15_reboundx', *args, **kwargs)
    return integrate_compiled(*args, **kwargs)


def check(case, years, variant):
    short_h, long_h = years[0]*YEAR, years[1]*YEAR
    grid = np.linspace(0.0, short_h, 41)
    short = integrate(case, short_h, variant, grid)
    long = integrate(case, long_h, variant, np.r_[grid, long_h])
    a = {s['time']: s['state'] for s in short['samples']}
    b = {s['time']: s['state'] for s in long['samples']}
    common = [t for t in a if t in b and t < short_h]
    assert len(common) >= 5
    assert all(a[t] == b[t] for t in common)  # побитно
    if short['event']:
        assert short['event'] == long['event']
        assert short['physical_outcome'] == long['physical_outcome']
    return short, long


@pytest.mark.parametrize('orbit, years, variant', [
    ('ganymede-outer-0-0.0', (0.01, 0.1), 'D'),      # контакт на 0.0058 года
    ('iapetus-outer-180-0.0', (0.05, 0.2), 'D'),     # выживает, длинные шаги
    ('iapetus-outer-180-0.0', (0.05, 0.2), 'T'),
    ('himalia-outer-90-0.3-phase', (0.2, 1.0), 'D'),  # высокие e, терминальная фаза (W2-D022)
])
def test_compiled_does_not_depend_on_horizon(cases, orbit, years, variant):
    check(cases[orbit], years, variant)


@pytest.mark.parametrize('orbit, years', [
    ('ganymede-outer-0-0.0', (0.01, 0.1)),
    ('iapetus-outer-180-0.0', (0.05, 0.2)),
])
def test_ias15_does_not_depend_on_horizon(cases, orbit, years):
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    check(cases[orbit], years, 'B')


def test_only_last_step_differs_at_short_horizon_end(cases):
    short, long = check(cases['iapetus-outer-180-0.0'], (0.05, 0.2), 'D')
    end = 0.05*YEAR
    a = {s['time']: s['state'][-6:] for s in short['samples']}
    b = {s['time']: s['state'][-6:] for s in long['samples']}
    assert end in a and end in b
    rel = np.linalg.norm(np.subtract(a[end][:3], b[end][:3]))/np.linalg.norm(a[end][:3])
    assert rel < 1e-9  # конец укороченного шага против интерполяции внутри шага
