"""Все минимумы расстояния внутри полинома DOP853, включая вход/выход между шагами."""

import numpy as np
from numpy.polynomial import chebyshev as cheb
from scipy.optimize import brentq
from submoon_research.dynamics.dense_segments import chebyshev_samples

NODES = np.cos(np.arange(8) * np.pi / 7)
INVERSE = np.linalg.inv(cheb.chebvander(NODES, 7))


# Запас векторной отсечки (W2-T008): во много порядков больше расхождения
# округления между пакетным и потельным вычислением нижней границы, поэтому
# тело пропускается только если прежняя потельная проверка его тоже пропустила.
SCREEN_MARGIN = 1e-9


def polynomial_contact(
    dense, t0, t1, body_count, radii, *, distance_tolerance=1e-7, time_tolerance=1e-5,
    samples=None,
):
    """samples — необязательный результат chebyshev_samples(dense, t0, t1) того же
    интервала, общий с другими проверками событий шага (W2-T008)."""
    if not t0 < t1 or len(radii) != body_count or np.any(np.asarray(radii) <= 0):
        raise ValueError("Неверная контактная постановка")
    samples, inverse = chebyshev_samples(dense, t0, t1) if samples is None else samples
    if samples.shape[1] != body_count + 1:
        raise ValueError('Число тел не совпало с dense-сегментом')
    if not np.isfinite(samples).all():
        raise ValueError("Неконечный dense output")
    # Пакетная предварительная отсечка всех тел одной операцией; для оставшихся
    # кандидатов ниже выполняется прежняя потельная проверка без изменений.
    relative = samples[:, -1:, :3] - samples[:, :body_count, :3]
    norms = np.linalg.norm(np.tensordot(inverse, relative, axes=(1, 0)), axis=2)
    screen = norms[0] - norms[1:].sum(axis=0)
    limit = (np.asarray(radii, dtype=float) + distance_tolerance
             + SCREEN_MARGIN*(norms.sum(axis=0) + 1.0))
    found = []
    for index in np.flatnonzero(screen <= limit):
        index = int(index)
        radius = radii[index]
        positions = samples[:, -1, :3] - samples[:, index, :3]
        coefficients = inverse @ positions
        # |Tk(x)|<=1 на [-1,1]: нижняя граница нормы всего полинома.
        lower = np.linalg.norm(coefficients[0]) - np.sum(np.linalg.norm(coefficients[1:], axis=1))
        if lower > radius + distance_tolerance:
            continue
        squared = np.zeros(2*len(coefficients)-1)
        for axis in range(3):
            part = cheb.chebmul(coefficients[:, axis], coefficients[:, axis])
            squared[: len(part)] += part
        roots = cheb.chebroots(cheb.chebder(squared))
        candidates = sorted(
            [-1.0, 1.0] + [float(z.real) for z in roots if abs(z.imag) < 1e-8 and -1 < z.real < 1]
        )

        def gap(x):
            return float(np.linalg.norm(cheb.chebval(x, coefficients)) - radius)

        previous = -1.0
        for current in candidates:
            value = gap(current)
            if value <= distance_tolerance:
                if gap(previous) <= 0:
                    entry = previous
                    kind = "reference_contact"
                elif value < 0:
                    entry = brentq(
                        gap,
                        previous,
                        current,
                        xtol=max(5e-16, min(1e-12, 2 * time_tolerance / (t1 - t0))),
                    )
                    kind = "reference_contact"
                else:
                    entry = current
                    kind = "reference_tangency_within_tolerance"
                time = t0 + (entry + 1) * (t1 - t0) / 2
                exact = np.asarray(dense(time)).reshape(body_count + 1, 6)
                residual = float(np.linalg.norm(exact[-1, :3] - exact[index, :3]) - radius)
                if abs(residual) > max(distance_tolerance * 10, 1e-8) and entry > -1:
                    raise ArithmeticError(
                        "Полиномиальная локализация не прошла независимую dense-проверку"
                    )
                found.append(
                    dict(
                        time=float(time),
                        body_index=index,
                        event=kind,
                        distance_residual_km=residual,
                        physical_surface_verified=False,
                    )
                )
                break
            previous = current
    return min(found, key=lambda item: item["time"]) if found else None
