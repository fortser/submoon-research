"""Независимый эталон с высокой точностью для фактически округлённого старта."""

import mpmath as mp
import numpy as np


def pericenter_seed(eccentricity):
    with mp.workdps(60):
        e = mp.mpf(str(eccentricity))
        return np.array([float(1 - e), 0.0, 0.0, 0.0, float(mp.sqrt((1 + e) / (1 - e))), 0.0])


def pericenter_reference(initial, times, *, digits=60):
    initial = np.asarray(initial, dtype=float)
    if (
        initial.shape != (6,)
        or not np.isfinite(initial).all()
        or initial[0] <= 0
        or initial[4] <= 0
        or np.any(initial[[1, 2, 3, 5]] != 0)
    ):
        raise ValueError("Эталон требует плоский перицентр x>0, vy>0, GM=1")
    with mp.workdps(digits):
        rp, vp = mp.mpf(float(initial[0])), mp.mpf(float(initial[4]))
        a = 1 / (2 / rp - vp**2)
        e = 1 - rp / a
        if not 0 <= e < 1:
            raise ValueError("Начальное состояние не эллиптический перицентр")
        n = a ** mp.mpf("-1.5")
        beta = mp.sqrt(1 - e**2)
        states = []
        max_residual = mp.mpf(0)
        for time in times:
            mean = mp.fmod(mp.mpf(float(time)) * n, 2 * mp.pi)
            if mean > mp.pi:
                mean -= 2 * mp.pi
            anomaly = mean
            low = -mp.pi
            high = mp.pi
            for _ in range(200):
                residual = anomaly - e * mp.sin(anomaly) - mean
                if abs(residual) < mp.mpf(10) ** (-(digits - 10)):
                    break
                if residual > 0:
                    high = anomaly
                else:
                    low = anomaly
                trial = anomaly - residual / (1 - e * mp.cos(anomaly))
                anomaly = trial if low < trial < high else (low + high) / 2
            else:
                raise ArithmeticError("Эталон Кеплера не сошёлся")
            max_residual = max(max_residual, abs(residual))
            c, s = mp.cos(anomaly), mp.sin(anomaly)
            states.append(
                [
                    float(a * (c - e)),
                    float(a * beta * s),
                    0.0,
                    float(-a * n * s / (1 - e * c)),
                    float(a * n * beta * c / (1 - e * c)),
                    0.0,
                ]
            )
        metadata = dict(
            digits=digits,
            a_from_rounded_seed=float(a),
            e_from_rounded_seed=float(e),
            energy_from_rounded_seed=float(-1 / (2 * a)),
            angular_momentum_from_rounded_seed=float(rp * vp),
            kepler_residual=float(max_residual),
            comparison="same_binary64_initial_state_not_ideal_decimal_elements",
        )
    return np.array(states), metadata
