"""W2-P014 step 1: fresh IAS15 restarts after a Galilean transform to the massive-body CM frame.

Diagnostic only. G: positions and velocities relative to the centre of mass of the massive
bodies (small coordinates, no drift). GF: same velocities, original huge positions (no drift,
huge coordinates). With P00 (huge, drifting) and P10 (small, drifting) of W2-P011 this is a
2x2 matrix: coordinate magnitude x frame drift. Forces depend only on relative positions;
J2 poles are fixed directions, so the transform is exact up to binary64 rounding.
"""
from __future__ import annotations

import math

import numpy as np

from submoon_research.contracts.p2_files import digest
from submoon_research.diagnostics.ias15_frame_controls import pair_differences
from submoon_research.diagnostics.ias15_memory_controls import integrate_window
from submoon_research.diagnostics.ias15_replay import adaptation

VARIANTS = ('G', 'GF')
PRS23_FACTOR = lambda epsilon: (epsilon*5040.)**(1/7)  # noqa: E731  sqrt7(epsilon*5040)


def cm_transform(physical, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown CM-frame variant: '+repr(variant))
    state = np.asarray(physical['absolute_state'], dtype=float)
    gms = np.asarray(physical['gms'], dtype=float)
    if len(gms) != len(state)-1 or not (gms > 0).all():
        raise ValueError('Expected positive GM for all bodies except the massless probe (last)')
    weights = np.r_[gms, 0.]
    cm = (weights[:, None]*state).sum(axis=0)/weights.sum()
    new = state.copy()
    new[:, 3:] -= cm[3:]
    if variant == 'G':
        new[:, :3] -= cm[:3]
    pos_exact = pair_differences(new[:, :3]).tobytes() == pair_differences(state[:, :3]).tobytes()
    vel_dev = float(np.max(np.abs(pair_differences(new[:, 3:])-pair_differences(state[:, 3:]))))
    vel_limit = float(np.max(np.spacing(np.abs(state[:, 3:]))))
    if not pos_exact:
        raise ValueError('Pairwise binary64 positions changed; stop')
    if vel_dev > vel_limit:
        raise ValueError(f'Pairwise velocities changed by {vel_dev:.3e} > 1 ULP {vel_limit:.3e}; stop')
    cm_after = (weights[:, None]*new).sum(axis=0)/weights.sum()
    return dict(variant=variant, absolute_state=new.tolist(), time=physical['time'],
                original_time=physical['time'], time_shift_s=0.,
                position_shift_km=(cm[:3] if variant == 'G' else np.zeros(3)).tolist(),
                velocity_shift_km_s=cm[3:].tolist(), cm_before=cm.tolist(), cm_after=cm_after.tolist(),
                gms=physical['gms'], figures=physical['figures'], dt=physical['dt'],
                adaptation=physical['adaptation'], pairwise_position_exact=pos_exact,
                max_pairwise_velocity_deviation_km_s=vel_dev, velocity_ulp_limit_km_s=vel_limit,
                max_abs_coordinate_km=float(np.max(np.abs(new[:, :3]))),
                coordinate_ulp_km=float(np.max(np.spacing(np.abs(new[:, :3])))),
                frame='Galilean: constant velocity boost (and for G constant translation); nonrotating axes',
                restart='new_adaptive_history', position_unit='km', velocity_unit='km/s', time_unit='s')


def run_cm(config, identity, grid, *, wall_seconds=60., max_steps=10000):
    if not 0 < wall_seconds <= 60 or not 1 <= max_steps <= 10000:
        raise ValueError('CM-frame controls limited to 60 s / 10000 attempts')
    if config['variant'] not in VARIANTS:
        raise ValueError('Unknown CM-frame variant')
    if config['adaptation']['adaptive_mode'] != 'PRS23' or config['adaptation']['min_dt'] != 0:
        raise ValueError('Unexpected adaptive mode')
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    native = NativeIAS15(config['absolute_state'], config['gms'], config['figures'],
                         epsilon=config['adaptation']['epsilon'], t0=config['time'], initial_dt=config['dt'])
    if adaptation(native) != config['adaptation']:
        raise ValueError('Fresh restart changed scalar adaptation settings')
    result = integrate_window(native, identity, grid, wall_seconds=wall_seconds, max_steps=max_steps)
    result.update(variant=config['variant'],
                  experimental_fingerprint=digest(dict(config=config, identity=identity, grid=grid,
                                                       wall_seconds=wall_seconds, max_steps=max_steps)))
    return result


def physical_prs23_dt(state, gms, epsilon):
    """Upper-bound PRS23 step for the probe from exact point-mass a and jerk (snap term ignored)."""
    state = np.asarray(state, dtype=float)
    p, v = state[-1, :3], state[-1, 3:]
    a, j = np.zeros(3), np.zeros(3)
    for i, gm in enumerate(gms):
        d, dv = state[i, :3]-p, state[i, 3:]-v
        r = float(np.linalg.norm(d))
        a += gm*d/r**3
        j += gm*(dv/r**3-3*d*np.dot(d, dv)/r**5)
    return math.sqrt(2.)*float(np.linalg.norm(a))/float(np.linalg.norm(j))*PRS23_FACTOR(epsilon)


PREDICTIONS = dict(
    rule=('Pre-registered before execution (W2-P014 step 1). Physical PRS23 estimate in the window ~9.9 s; '
          'P10 (small coordinates) min dt 9.909 s; P00 (huge coordinates) 0.404 s.'),
    confirm=('G target_reached and min dt >= 5 s, and GF guard or min dt <= 1 s '
             '(min dt excludes the final target-limited step)'),
    coordinate_noise_rejected='G and GF both reach target with min dt >= 5 s (boost, not magnitude, matters)',
    hypothesis_rejected='G guard or min dt < 5 s',
    other='any other combination (including wall/step budget exhaustion): inconclusive',
    trajectory='descriptive only: max |dr| of G vs P00/P10 at common grid; flag if > 10 m',
)


def min_unclipped_dt(result):
    """Minimum dt_done excluding the final step that was limited by the common target."""
    values = [r['dt_done'] for r in result['records'] if r['right'] < result['target_time']]
    return min(values) if values else None


def classify(g, gf):
    # W2-T047: исчерпание бюджета не означает guard/опровержение гипотезы.
    # Исторические G/GF оба достигли target; их вердикт этим не меняется.
    if any(r.get('status') not in ('target_reached', 'guard') for r in (g, gf)):
        return 'inconclusive'
    g_min, gf_min = min_unclipped_dt(g), min_unclipped_dt(gf)
    if g['status'] == 'guard':
        return 'hypothesis_rejected'
    if g_min is None or not math.isfinite(g_min):
        return 'inconclusive'
    if g_min < 5.:
        return 'hypothesis_rejected'
    if not g['target_reached']:
        return 'inconclusive'
    if gf['status'] == 'guard':
        return 'confirm'
    if not gf['target_reached'] or gf_min is None or not math.isfinite(gf_min):
        return 'inconclusive'
    if gf_min <= 1.:
        return 'confirm'
    if gf_min >= 5.:
        return 'coordinate_noise_rejected'
    return 'inconclusive'
