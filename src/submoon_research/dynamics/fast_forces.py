"""FP64 ядра A/C. Валидация входа выполняется до горячего цикла."""
import numpy as np
from numba import njit


def figure_array(figures, count):
    result = np.empty((len(figures), 7), dtype=np.float64)
    for k, f in enumerate(figures):
        result[k] = [f['index'], f['j2'], f['radius'], *f['pole'], 0.0]
        if not 0 <= f['index'] < count or f['radius'] <= 0:
            raise ValueError('Неверный источник фигуры')
        if abs(np.linalg.norm(f['pole']) - 1) > 1e-12:
            raise ValueError('Полюс должен быть нормирован')
    if not np.isfinite(result).all():
        raise ValueError('Неконечная фигура')
    return result


@njit(cache=True, fastmath=False)
def j2_vector(r, gm, j2, radius, pole):
    d2 = np.dot(r, r)
    z = np.dot(r, pole)
    factor = 1.5 * gm * j2 * radius**2 / (d2**2 * np.sqrt(d2))
    return factor * ((5 * z*z / d2 - 1) * r - 2*z*pole)


@njit(cache=True, fastmath=False)
def accelerations(positions, gms, figures):
    count = len(gms)
    out = np.zeros_like(positions)
    for i in range(count):
        for j in range(i):
            delta = positions[j] - positions[i]
            d2 = np.dot(delta, delta)
            base = delta / (d2 * np.sqrt(d2))
            out[i] += gms[j] * base
            out[j] -= gms[i] * base
    for f in figures:
        i = int(f[0])
        for j in range(count):
            if j != i:
                force = j2_vector(positions[j]-positions[i], gms[i], f[1], f[2], f[3:6])
                out[j] += force
                out[i] -= gms[j]/gms[i] * force
    return out


@njit(cache=True, fastmath=False)
def probe_acceleration(probe, massive_positions, gms, figures):
    # Монополь хозяина и разность внешних ускорений в фиксированных осях.
    r = probe[:3]
    d2 = np.dot(r, r)
    out = -gms[0]*r/(d2*np.sqrt(d2))
    for i in range(1, len(gms)):
        p = massive_positions[i]
        delta = p-r
        p2, s2 = np.dot(p, p), np.dot(delta, delta)
        out += gms[i]*(delta/(s2*np.sqrt(s2))-p/(p2*np.sqrt(p2)))
    for f in figures:
        i = int(f[0])
        out += j2_vector(r-massive_positions[i], gms[i], f[1], f[2], f[3:6])
        if i != 0:
            out -= j2_vector(-massive_positions[i], gms[i], f[1], f[2], f[3:6])
        else:
            # Реакция хозяина на массивных партнёров, не на безмассовый объект.
            for j in range(1, len(gms)):
                out += gms[j]/gms[0]*j2_vector(
                    massive_positions[j], gms[0], f[1], f[2], f[3:6])
    return out


@njit(cache=True, fastmath=False)
def rhs(state, gms, figures):
    rows = state.reshape((-1, 6))
    count = len(gms)
    out = np.zeros_like(rows)
    acc = accelerations(rows[:count, :3], gms, figures)
    out[1:count, :3] = rows[1:count, 3:]
    out[1:count, 3:] = acc[1:] - acc[0]
    out[-1, :3] = rows[-1, 3:]
    out[-1, 3:] = probe_acceleration(rows[-1], rows[:count, :3], gms, figures)
    return out.ravel()


@njit(cache=True, fastmath=False)
def energy(state, gms, figures):
    rows = state.reshape((-1, 6))
    center_v = np.zeros(3)
    for i in range(len(gms)):
        center_v += gms[i]*rows[i, 3:]
    center_v /= np.sum(gms)
    total = 0.0
    for i in range(len(gms)):
        v = rows[i, 3:] - center_v
        total += gms[i]*np.dot(v, v)/2
        for j in range(i):
            r = rows[i, :3]-rows[j, :3]
            total -= gms[i]*gms[j]/np.sqrt(np.dot(r, r))
    for f in figures:
        i = int(f[0])
        for j in range(len(gms)):
            if i != j:
                r = rows[j, :3]-rows[i, :3]
                radius = np.sqrt(np.dot(r, r))
                cosine = np.dot(r, f[3:6])/radius
                total += gms[i]*gms[j]*f[1]*f[2]**2*(3*cosine**2-1)/(2*radius**3)
    return total
