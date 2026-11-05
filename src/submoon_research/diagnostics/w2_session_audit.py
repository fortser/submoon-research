"""Независимая локальная арифметика аудита W2-T047; без интегрирования орбит.

Единицы: km, s, GM в km^3/s^2; безмассовая проба последняя.
np.longdouble имеет платформенную точность (в WSL x86_64 шире binary64).
"""
import numpy as np


def derivatives(state, gms):
    """Ньютоновские a, jerk, snap пробы по локальному состоянию; longdouble."""
    s = np.asarray(state, dtype=np.longdouble)
    gm = np.r_[np.asarray(gms, dtype=np.longdouble), np.longdouble(0)]
    a = np.zeros((len(s), 3), dtype=np.longdouble)
    terms = []
    for i in range(len(s)):
        for k in range(len(gms)):
            if k != i:
                d = s[k, :3]-s[i, :3]
                a[i] += gm[k]*d/np.linalg.norm(d)**3
    jerk = np.zeros(3, dtype=np.longdouble)
    snap = jerk.copy()
    for k in range(len(gms)):
        d, v, da = s[k, :3]-s[-1, :3], s[k, 3:]-s[-1, 3:], a[k]-a[-1]
        r, q = np.linalg.norm(d), np.dot(d, v)
        terms.append(gm[k]*d/r**3)
        jerk += gm[k]*(v/r**3-3*d*q/r**5)
        snap += gm[k]*(da/r**3-6*v*q/r**5-3*d*(np.dot(v, v)+np.dot(d, da))/r**5
                      +15*d*q*q/r**7)
    return a[-1], jerk, snap, np.array(terms)


def j2_probe(state, gms, figures):
    """Абсолютное ускорение пробы от J2 с фиксированными нормированными осями."""
    s = np.asarray(state, dtype=np.longdouble)
    total = np.zeros(3, dtype=np.longdouble)
    for f in figures:
        i = f['index']
        d, pole = s[-1, :3]-s[i, :3], np.asarray(f['pole'], dtype=np.longdouble)
        r2, z = np.dot(d, d), np.dot(d, pole)
        total += (1.5*gms[i]*f['j2']*f['radius']**2/r2**2.5)*((5*z*z/r2-1)*d-2*z*pole)
    return total
