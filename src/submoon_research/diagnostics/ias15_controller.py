"""Пассивная реконструкция PRS23 из буферов завершённого шага REBOUND 5.1.1.

Источник: integrator_ias15.c tag 5.1.1, SHA в постановке W2-T037.
Это не реализация интегратора и не изменение его адаптации.
"""
from __future__ import annotations

import math
import sys

import numpy as np


def normal(x):
    return math.isfinite(x) and abs(x) >= sys.float_info.min


def sqrt7(a):
    """Порядок скалярных операций upstream sqrt7, для положительного normal a."""
    if not normal(a) or a <= 0:
        raise ValueError('sqrt7 требует положительный normal аргумент')
    scale = 1.
    while a < 1e-7:
        scale *= .1
        a *= 1e7
    while a > 1e2:
        scale *= 10.
        a *= 1e-7
    x = 1.
    for _ in range(20):
        x6 = x*x*x*x*x*x
        x += (a/x6-x)/7.
    return x*scale


def reconstruct_prs23(a0, br, dt_done, epsilon, min_dt=0.):
    """br, не предсказанный b; y3/y4 имеют размерность ускорения в квадрате."""
    a0, br = np.asarray(a0, dtype=float), np.asarray(br, dtype=float)
    if (a0.ndim != 2 or a0.shape[1] != 3 or br.shape != (7, *a0.shape)
            or not np.isfinite(a0).all() or not np.isfinite(br).all()
            or not math.isfinite(dt_done) or dt_done <= 0 or not math.isfinite(epsilon)
            or epsilon <= 0 or not math.isfinite(min_dt) or min_dt < 0):
        raise ValueError('Неверный контракт реконструкции PRS23')
    factor = sqrt7(epsilon*5040.)
    rows = []
    for i in range(len(a0)):
        a02 = y2 = y3 = y4 = 0.
        for k in range(3):
            a = float(a0[i, k])
            b0, b1, b2, b3, b4, b5, b6 = map(float, br[:, i, k])
            a02 += a*a
            v = a+b0+b1+b2+b3+b4+b5+b6
            y2 += v*v
            v = b0+2.*b1+3.*b2+4.*b3+5.*b4+6.*b5+7.*b6
            y3 += v*v
            v = 2.*b1+6.*b2+12.*b3+20.*b4+30.*b5+42.*b6
            y4 += v*v
        denom = y3+math.sqrt(y4*y2)
        tau2 = 2.*y2/denom if denom > 0 else math.inf
        valid = normal(a02) and normal(tau2) and tau2 > 0
        rows.append(dict(body=i, a0_squared=a02, y2=y2, y3=y3, y4=y4,
                         scaled_timescale_squared=tau2 if math.isfinite(tau2) else None,
                         candidate_dt=math.sqrt(tau2)*dt_done*factor if valid else None,
                         jerk_norm=math.sqrt(y3)/dt_done,
                         snap_norm=math.sqrt(y4)/dt_done**2))
    eligible = [row for row in rows if row['candidate_dt'] is not None]
    selected = min(eligible, key=lambda row: row['scaled_timescale_squared']) if eligible else None
    raw = selected['candidate_dt'] if selected else 4.*dt_done
    proposed = max(raw, min_dt)
    # Если отношение <.25, источник отверг бы эту попытку до возврата Native.step.
    rejected = proposed/dt_done < .25
    if proposed/dt_done > 4.:
        proposed = 4.*dt_done
    return dict(per_body=rows, limiting_body=None if selected is None else selected['body'],
                raw_next_dt=raw, predicted_next_dt=proposed, rejection_predicted=rejected,
                factor=factor, scope='accepted internal attempt only; no rejected attempts/PC iterations')


def geometry(state, dt_done, gm_host):
    state = np.asarray(state, dtype=float)
    if (state.ndim != 2 or state.shape[1] != 6 or len(state) < 2
            or not np.isfinite(state).all() or dt_done <= 0 or gm_host <= 0):
        raise ValueError('Неверная геометрия диагностики')
    r = state[-1, :3]-state[0, :3]
    v = state[-1, 3:]-state[0, 3:]
    radius = float(np.linalg.norm(r))
    if radius == 0:
        raise ValueError('Нулевое расстояние пробы')
    ulps = np.abs(np.spacing(state[:, :3]))
    pair_ulps = np.maximum(ulps[0], ulps[-1])
    with np.errstate(over='ignore'):
        ratios = np.abs(v)*dt_done/pair_ulps
    return dict(host_probe_radius_km=radius, relative_speed_km_s=float(np.linalg.norm(v)),
                local_period_s=2*math.pi*math.sqrt(radius**3/gm_host),
                absolute_position_max_km=float(np.max(abs(state[:, :3]))),
                position_ulp_km=ulps.tolist(),
                relative_linear_motion_over_coordinate_ulp=[float(x) if math.isfinite(x) else None for x in ratios],
                relative_motion_ratio_overflow=[not math.isfinite(x) for x in ratios],
                minimum_pair_distance_km=min(float(np.linalg.norm(a[:3]-b[:3]))
                    for i, a in enumerate(state) for b in state[i+1:]))
