"""W2-P011: isolated physical restarts; not a production integrator/force change."""
from __future__ import annotations

import math
import time
from itertools import combinations

import numpy as np

from submoon_research.contracts.p2_files import digest
from submoon_research.diagnostics.ias15_controller import reconstruct_prs23
from submoon_research.diagnostics.ias15_replay import adaptation, guard_reason
from submoon_research.dynamics.engine_compare import ias15_step_limit
from submoon_research.dynamics.native_ias15 import NativeIAS15

VARIANTS = ('P00', 'P10', 'P01', 'P11')


def pair_differences(state):
    state = np.asarray(state, dtype=float)
    return np.array([state[j]-state[i] for i, j in combinations(range(len(state)), 2)])


def transform(physical, variant):
    if variant not in VARIANTS:
        raise ValueError('Unknown physical variant')
    state = np.asarray(physical['absolute_state'], dtype=float).copy()
    original = state.copy()
    shift_x = state[0, :3].copy() if variant[1] == '1' else np.zeros(3)
    shift_t = physical['time'] if variant[2] == '1' else 0.
    state[:, :3] -= shift_x
    before, after = pair_differences(original), pair_differences(state)
    exact = before.tobytes() == after.tobytes()
    if not exact:
        raise ValueError('Initial pairwise binary64 geometry changed; stop matrix')
    return dict(variant=variant, absolute_state=state.tolist(), time=physical['time']-shift_t,
                original_time=physical['time'], position_shift_km=shift_x.tolist(), time_shift_s=shift_t,
                gms=physical['gms'], figures=physical['figures'], dt=physical['dt'],
                adaptation=physical['adaptation'], initial_pairwise_exact=exact,
                initial_pairs_sha256=digest(before.tolist()),
                frame='constant position translation only; nonrotating axes; unchanged velocities',
                restart='new_adaptive_history', position_unit='km', velocity_unit='km/s', time_unit='s')


def common_grid(t0, tf, after=1000.):
    target_abs = float(tf+after)
    absolute = np.unique(np.r_[t0+np.linspace(0., target_abs-t0, 65), tf])
    elapsed = absolute-t0
    if not np.array_equal(t0+elapsed, absolute):
        raise ValueError('Grid time mapping is not exact')
    return dict(absolute=absolute.tolist(), elapsed=elapsed.tolist(), target_elapsed=float(elapsed[-1]),
                historical_failure_elapsed=tf-t0, original_t0=t0)


def _relative(state):
    state = np.asarray(state, dtype=float)
    return state-state[:1]


def run_physical(config, identity, grid, *, wall_seconds=60., max_steps=10000):
    if not 0 < wall_seconds <= 60 or not 1 <= max_steps <= 10000:
        raise ValueError('Physical controls limited to 60 s / 10000 attempts')
    if config['adaptation']['adaptive_mode'] != 'PRS23' or config['adaptation']['min_dt'] != 0:
        raise ValueError('Unexpected adaptive mode')
    native = NativeIAS15(config['absolute_state'], config['gms'], config['figures'],
                         epsilon=config['adaptation']['epsilon'], t0=config['time'], initial_dt=config['dt'])
    if adaptation(native) != config['adaptation']:
        raise ValueError('Physical restart changed scalar adaptation settings')
    start_t = float(native.sim.t)
    target = start_t+grid['target_elapsed']
    sample_times = start_t+np.asarray(grid['elapsed'])
    if not np.array_equal(sample_times+config['time_shift_s'], grid['absolute']):
        raise ValueError('Variant grid does not map to common absolute times')
    started, cpu = time.monotonic(), time.process_time()
    final_state = _relative(native.absolute_state())
    samples = [dict(index=0, elapsed=grid['elapsed'][0], time=float(sample_times[0]),
                    relative_state=final_state.tolist(), kind='initial')]
    records, pending, accepted = [], 1, 0
    status, failure, last_valid_time = 'step_budget', None, start_t
    for index in range(max_steps):
        if time.monotonic()-started >= wall_seconds:
            status = 'wall_budget'
            break
        left, proposed = float(native.sim.t), float(native.sim.dt)
        before_state = native.absolute_state()
        maximum = ias15_step_limit(native, identity['period_seconds'], identity['gm_host'], identity['step_fraction'])
        try:
            segment = native.step(target, maximum)
        except (ArithmeticError, ValueError, RuntimeError) as exc:
            failure = dict(reason=guard_reason(exc, left, float(native.sim.t), target),
                           error=str(exc), left=left, right=float(native.sim.t),
                           elapsed_left=left-start_t, dt_done=float(native.sim.dt_last_done),
                           next_dt=float(native.sim.dt))
            status = 'guard'
        dt = float(native.sim.dt_last_done)
        a0 = native._array('a0', 3*native.count).reshape(native.count, 3)
        br = native._array('br', 21*native.count).reshape(7, native.count, 3)
        control = reconstruct_prs23(a0, br, dt, float(native.sim.integrator.epsilon))
        records.append(dict(index=index, left=left, elapsed_left=left-start_t,
                            right=float(native.sim.t), proposed_dt=proposed,
                            max_step=maximum, dt_done=dt, next_dt=float(native.sim.dt),
                            time_ulp=math.ulp(left), dt_over_time_ulp=dt/math.ulp(left) if left else None,
                            host_probe_radius_km=float(np.linalg.norm(before_state[-1, :3]-before_state[0, :3])),
                            limiting_body=control['limiting_body'],
                            candidate_dt_per_body=[r['candidate_dt'] for r in control['per_body']],
                            failure=failure))
        if failure:
            break
        accepted += 1
        last_valid_time = float(native.sim.t)
        final_state = _relative(native.absolute_state())
        while pending < len(sample_times) and sample_times[pending] <= segment.right:
            when = float(sample_times[pending])
            if when < segment.left:
                raise ValueError('Common sample lost; no nearest-step substitution allowed')
            samples.append(dict(index=pending, elapsed=grid['elapsed'][pending], time=when,
                                relative_state=np.asarray(segment(when)).reshape(native.count, 6).tolist(),
                                kind='dense'))
            pending += 1
        if last_valid_time >= target:
            status = 'target_reached'
            break
    dt_values = [r['dt_done'] for r in records]
    return dict(variant=config['variant'], status=status, failure=failure, attempts=len(records),
                accepted_steps=accepted, last_valid_time=last_valid_time,
                last_valid_elapsed=last_valid_time-start_t, target_time=target,
                target_elapsed=grid['target_elapsed'], target_reached=status == 'target_reached',
                wall_seconds=time.monotonic()-started, cpu_seconds=time.process_time()-cpu,
                dt_quantiles_s=np.quantile(dt_values, [0, .1, .5, .9, 1]).tolist() if dt_values else None,
                final_accepted_relative_state=final_state.tolist(), samples=samples, records=records,
                experimental_fingerprint=digest(dict(config=config, identity=identity, grid=grid,
                                                     wall_seconds=wall_seconds, max_steps=max_steps)),
                scientific_admission=False, scope='native-step diagnostic only; no event tracker')


def compare_samples(first, second, *, radius_scale, speed_scale):
    if radius_scale <= 0 or speed_scale <= 0:
        raise ValueError('Positive physical normalization scales required')
    by_index = {r['index']: r for r in second['samples']}
    differences = []
    for a in first['samples']:
        if a['index'] not in by_index:
            continue
        b = by_index[a['index']]
        if a['elapsed'] != b['elapsed']:
            raise ValueError('Samples have different elapsed times')
        delta = np.asarray(a['relative_state'])[-1]-np.asarray(b['relative_state'])[-1]
        position, speed = float(np.linalg.norm(delta[:3])), float(np.linalg.norm(delta[3:]))
        differences.append(dict(index=a['index'], elapsed=a['elapsed'], position_difference_km=position,
                                speed_difference_km_s=speed, normalized_position=position/radius_scale,
                                normalized_speed=speed/speed_scale))
    return dict(common_points=len(differences), differences=differences,
                max_common_elapsed=max((d['elapsed'] for d in differences), default=None),
                max_position_difference_km=max((d['position_difference_km'] for d in differences), default=None),
                max_speed_difference_km_s=max((d['speed_difference_km_s'] for d in differences), default=None),
                max_normalized_position=max((d['normalized_position'] for d in differences), default=None),
                max_normalized_speed=max((d['normalized_speed'] for d in differences), default=None),
                trajectory_admission=False)
