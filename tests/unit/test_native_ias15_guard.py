"""W2-I015, W2-I017: страж согласованности полинома IAS15 и округление времени REBOUND.

REBOUND хранит t = fl(t + dt_done); right-left отличается от фактического шага до ulp(t)/2.
Прежняя покомпонентная нормировка давала ложный отказ, когда компонента положения тела
проходила через ноль на больших t (наблюдалось у B на 1.3-8.8 года в P2-1).
"""
import math

import numpy as np
import pytest

from submoon_research.dynamics.native_ias15 import (ENDPOINT_RESIDUAL_LIMIT, component_residual,
                                                     endpoint_residual)


def test_time_label_error_on_zero_crossing_component_is_not_a_defect():
    # Тело далеко от начала координат, x-компонента проходит через ноль; ошибка метки
    # времени 3e-8 с при 30 км/с даёт 9e-7 км по x.
    end = np.array([[0.0, 7.8e8, 1.0e7, 30.0, 0.5, 0.1]])
    poly = end.copy()
    poly[0, 0] += 30.0*3e-8
    poly[0, 3] += 1e-4*3e-8
    assert component_residual(poly, end) > ENDPOINT_RESIDUAL_LIMIT  # прежняя мера: ложный отказ
    assert endpoint_residual(poly, end) < 1e-12


def test_gross_defects_are_still_detected():
    end = np.array([[1.0e6, -2.0e5, 3.0e4, 10.0, -5.0, 1.0],
                    [7.8e8, 1.0e8, 0.0, 13.0, 1.0, 0.0]])
    poly = end.copy()
    poly[0, :3] *= 1+1e-7
    assert endpoint_residual(poly, end) > ENDPOINT_RESIDUAL_LIMIT
    poly = end.copy()
    poly[1, 4] += 1e-6
    assert endpoint_residual(poly, end) > ENDPOINT_RESIDUAL_LIMIT
    assert not math.isfinite(endpoint_residual(end*np.nan, end))
    assert endpoint_residual(end, end) == 0.0


def test_native_ias15_steps_at_large_time_pass_guard():
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    from submoon_research.dynamics.native_ias15 import NativeIAS15

    gm, radius = 1.26686534e8, 1.0e5
    speed = math.sqrt(gm/radius)
    state = np.array([[0, 0, 0, 0, 0, 0], [radius, 0, 0, 0, speed, 0]], dtype=float)
    native = NativeIAS15(state, [gm], [], t0=float(2**31), initial_dt=10.0)  # ulp(t0) ~ 4.8e-7 с
    worst = 0.0
    for _ in range(600):  # ~десятки оборотов, ~сотни проходов компонент через ноль
        native.step(native.sim.t+1.0e6)
        worst = max(worst, native.last_endpoint_residual)
    assert worst < ENDPOINT_RESIDUAL_LIMIT/5  # ожидаемо <= ~1e-10: v*ulp(t)/2/|r|


@pytest.mark.parametrize('t0', [0.0, float(2**31), float(2**34+12345), float(2**36)])
def test_endpoint_residual_independent_of_time_label(t0):
    # W2-I017: REBOUND хранит t = fl(t + dt_done); полином, собранный на метке right-left,
    # давал остаток ~ ulp(t) (отказы ganymede-inner B сразу после 2**34 с = 544.4 года).
    # Полином на sim.dt_last_done воспроизводит конец шага на уровне машинной точности при любом t.
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    from submoon_research.dynamics.native_ias15 import NativeIAS15

    gm, radius = 1.26686534e8, 1.0e5
    speed = math.sqrt(gm/radius)
    state = np.array([[0, 0, 0, 0, 0, 0], [radius, 0, 0, 0, speed, 0]], dtype=float)
    native = NativeIAS15(state, [gm], [], t0=t0, initial_dt=10.0)
    worst = 0.0
    for _ in range(600):
        native.step(native.sim.t+1.0e6)
        worst = max(worst, native.last_endpoint_residual)
    assert worst < 1e-12


def test_rejected_step_residual_is_reported():
    # W2-I017: остаток отклонённого шага сохраняется и попадает в текст ошибки
    # (прежде в запись попадал только максимум по принятым шагам).
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    from submoon_research.dynamics import native_ias15 as nm

    gm, radius = 1.26686534e8, 1.0e5
    state = np.array([[0, 0, 0, 0, 0, 0], [radius, 0, 0, 0, math.sqrt(gm/radius), 0]], dtype=float)
    native = nm.NativeIAS15(state, [gm], [], t0=0.0, initial_dt=10.0)
    original = native._assemble
    native._assemble = lambda start, a0, b, dt: original(start, a0, b, dt*(1+1e-6))  # грубый дефект
    with pytest.raises(ArithmeticError, match='остаток'):
        native.step(1.0e6)
    assert native.checked_endpoint_residual > nm.ENDPOINT_RESIDUAL_LIMIT
