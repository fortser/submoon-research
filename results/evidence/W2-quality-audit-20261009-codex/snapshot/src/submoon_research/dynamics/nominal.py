"""Самосогласованные массивные тела и один независимый безмассовый объект."""

import numpy as np
from submoon_research.forces.gravity import relative_monopoles, j2_acceleration


def massive_accelerations(positions, gms, figures=()):
    positions = np.asarray(positions, dtype=float)
    gms = np.asarray(gms, dtype=float)
    if (
        positions.shape != (len(gms), 3)
        or (gms <= 0).any()
        or not np.isfinite(np.r_[positions.ravel(), gms]).all()
    ):
        raise ValueError("Неверное состояние массивной системы")
    delta = positions[None, :, :] - positions[:, None, :]
    distances = np.linalg.norm(delta, axis=2)
    np.fill_diagonal(distances, np.inf)
    if (distances == 0).any():
        raise ValueError("Совпавшие массивные центры")
    accelerations = np.sum(delta * gms[None, :, None] / distances[:, :, None] ** 3, axis=1)
    for figure in figures:
        index = figure["index"]
        for other in range(len(gms)):
            if other == index:
                continue
            extra = j2_acceleration(
                positions[other] - positions[index],
                gms[index],
                figure["j2"],
                figure["radius"],
                figure["pole"],
            )
            accelerations[other] += extra
            accelerations[index] -= gms[other] / gms[index] * extra
    return accelerations


def relative_rhs(state, gms, figures=()):
    """Строка 0 — хозяин; его r/v точно нулевые, оси инерциальные."""
    states = np.asarray(state).reshape(-1, 6)
    if len(states) != len(gms) + 1 or np.any(states[0] != 0):
        raise ValueError("Хозяин должен быть нулём относительного состояния")
    massive = states[:-1]
    probe = states[-1]
    accelerations = massive_accelerations(massive[:, :3], gms, figures)
    result = np.zeros_like(states)
    result[1:-1, :3] = massive[1:, 3:]
    result[1:-1, 3:] = accelerations[1:] - accelerations[0]
    result[-1, :3] = probe[3:]
    result[-1, 3:] = relative_monopoles(probe[:3], gms[0], massive[1:, :3], gms[1:])
    # Origin reaction from planet figures is already in host acceleration.
    # Direct field difference keeps the probe acceleration in the same frame.
    for figure in figures:
        index = figure["index"]
        for target, sign in ((probe[:3], 1.0), (np.zeros(3), -1.0)):
            result[-1, 3:] += sign * j2_acceleration(
                target - massive[index, :3],
                gms[index],
                figure["j2"],
                figure["radius"],
                figure["pole"],
            )
    return result.ravel()


def massive_energy(state, gms, figures=()):
    states = np.asarray(state).reshape(-1, 6)[: len(gms)]
    gms = np.asarray(gms)
    velocity = states[:, 3:] - np.sum(gms[:, None] * states[:, 3:], axis=0) / gms.sum()
    value = np.sum(gms * np.sum(velocity**2, axis=1)) / 2
    for i in range(len(gms)):
        for j in range(i):
            value -= gms[i] * gms[j] / np.linalg.norm(states[i, :3] - states[j, :3])
    for figure in figures:
        i = figure["index"]
        for j in range(len(gms)):
            if i == j:
                continue
            delta = states[j, :3] - states[i, :3]
            radius = np.linalg.norm(delta)
            cosine = np.dot(delta, figure["pole"]) / radius
            value += (
                gms[i]
                * gms[j]
                * figure["j2"]
                * figure["radius"] ** 2
                * (3 * cosine * cosine - 1)
                / (2 * radius**3)
            )
    return float(value)
