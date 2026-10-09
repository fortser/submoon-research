"""W2-I015: страж согласованности полинома IAS15 и округление времени REBOUND.

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
