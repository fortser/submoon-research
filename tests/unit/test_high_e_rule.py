"""W2-D022: правило высоких e в прототипе v5 (синтетические кеплеровы орбиты, одно тело-хозяин).

Флаг ставится при связанном оскулирующем e >= 0.9; дальше счёт идёт не позже t1 + 10 P0:
контакт/уход до этого момента — исход принимается (terminal), иначе останов и unresolved
(prolonged); если горизонт раньше t1 + 10 P0 — unresolved (open_at_horizon).
"""
import math

import numpy as np
import pytest

from submoon_research.dynamics import compiled_dop853 as cd

GM, RADIUS = 1.0e4, 2634.0


def kepler_at_apocenter(a, e):
    apo = a*(1+e)
    speed = math.sqrt(GM*(1-e)/(a*(1+e)))
    period = 2*math.pi*math.sqrt(a**3/GM)
    state = np.array([[0, 0, 0, 0, 0, 0], [apo, 0, 0, 0, speed, 0]], dtype=float)
    return state, period


def run(a, e, periods):
    state, period = kepler_at_apocenter(a, e)
    out = cd.integrate_compiled(state, [GM], [], [RADIUS], periods*period, period=period)
    return out, period


def test_prolonged_high_e_stops_at_ten_periods_unresolved():
    out, period = run(60000.0, 0.95, 30)  # перицентр 3000 км > радиуса: контакта нет
    assert out['run_status'] == 'completed' and out['event'] is None
    assert out['physical_outcome'] == 'unresolved' and out['unresolved_reason'] == 'high_e_prolonged'
    nd = out['numerical_domain']
    assert nd['outside_validated_domain'] and nd['high_e_phase'] == 'prolonged'
    assert out['eccentricity_monitor']['first_bound_at_limit_time'] == 0.0
    assert out['last_valid_time'] == pytest.approx(cd.HIGH_E_TERMINAL_PERIODS*period, rel=1e-12)
    assert nd['high_e_stop_time'] == pytest.approx(out['last_valid_time'], rel=1e-12)


def test_terminal_high_e_contact_is_accepted():
    out, _ = run(30000.0, 0.95, 30)  # перицентр 1500 км < радиуса: контакт за пол-оборота
    assert out['physical_outcome'] == 'host_contact' and out['unresolved_reason'] is None
    assert out['numerical_domain']['high_e_phase'] == 'terminal'
    assert out['event']['time'] <= out['numerical_domain']['high_e_stop_time']


def test_high_e_open_at_horizon_is_unresolved():
    out, period = run(60000.0, 0.95, 3)
    assert out['physical_outcome'] == 'unresolved'
    assert out['unresolved_reason'] == 'high_e_open_at_horizon'
    assert out['numerical_domain']['high_e_phase'] == 'open_at_horizon'
    assert out['last_valid_time'] == pytest.approx(3*period, rel=1e-12)


def test_regular_orbit_unaffected():
    out, _ = run(60000.0, 0.1, 2)
    assert out['physical_outcome'] == 'survived' and out['unresolved_reason'] is None
    nd = out['numerical_domain']
    assert not nd['outside_validated_domain'] and nd['high_e_phase'] is None


def test_stop_does_not_change_steps_before_it():
    # Граница t1 + K·P0 укорачивает только последний шаг: точки до неё совпадают побитно
    # с расчётом, где горизонт короче (сетка из общих моментов).
    state, period = kepler_at_apocenter(60000.0, 0.95)
    times = np.linspace(0, 3*period, 31)
    long = cd.integrate_compiled(state, [GM], [], [RADIUS], 30*period, period=period,
                                 times=np.r_[times, 30*period])
    short = cd.integrate_compiled(state, [GM], [], [RADIUS], 3*period, period=period, times=times)
    a = {s['time']: s['state'] for s in long['samples']}
    b = {s['time']: s['state'] for s in short['samples']}
    common = [t for t in b if t in a and t < 3*period]
    assert len(common) == 30
    assert all(a[t] == b[t] for t in common)


def test_prototype_version_and_fingerprint_include_rule():
    assert cd.PROTOTYPE_VERSION == 5 and cd.HIGH_E_TERMINAL_PERIODS == 10.0
    assert len(cd.implementation_fingerprint()) == 64
