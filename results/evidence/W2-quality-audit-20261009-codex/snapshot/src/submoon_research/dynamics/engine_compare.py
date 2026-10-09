"""Общий полный путь A/B/C: силы, dense-события, диагностика и state restart."""
from dataclasses import asdict, dataclass
import hashlib
import json
import time

import numpy as np
from scipy.integrate import DOP853

from submoon_research.dynamics import fast_forces
from submoon_research.dynamics.dense_segments import CombinedSegment, chebyshev_samples
from submoon_research.dynamics.native_ias15 import MassiveCache, NativeIAS15
from submoon_research.dynamics.nominal import relative_rhs, massive_energy
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.events.escape import EscapeTracker


ENGINES = ('dop853_jit', 'ias15_reboundx', 'hierarchical_cached')


@dataclass(frozen=True)
class NumericMode:
    rtol: float = 1e-11
    atol_position: float = 1e-8
    atol_velocity: float = 1e-11
    step_fraction: float = 0.025
    epsilon: float = 1e-9
    cache_epsilon: float = 1e-12

    @classmethod
    def tight(cls):
        return cls(1e-13, 1e-10, 1e-13, 0.0125, 1e-12, 1e-13)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
        separators=(',', ':')).encode()).hexdigest()


def integrate_engine(engine, initial, gms, figures, radii, horizon, *, period,
                     mode=None, hill_km=None, escape_window=None, times=None,
                     budget=lambda: None, cache=None, resume=None, progress=None):
    if engine not in (*ENGINES, 'dop853_original'):
        raise ValueError('Неизвестный движок')
    mode = mode or NumericMode()
    initial = np.asarray(initial, dtype=float).reshape(-1, 6)
    gms = np.asarray(gms, dtype=float)
    radii = np.asarray(radii, dtype=float)
    if (initial.shape != (len(gms)+1, 6) or np.any(initial[0] != 0)
            or not np.isfinite(initial).all() or not np.isfinite(gms).all()
            or np.any(gms <= 0) or radii.shape != gms.shape or np.any(radii <= 0)
            or not np.isfinite(radii).all() or not horizon > 0 or not period > 0):
        raise ValueError('Неверные физические входы движка')
    fs = fast_forces.figure_array(figures, len(gms))
    contract = fingerprint(dict(initial=initial.tolist(), gms=gms.tolist(),
        figures=figures, radii=radii.tolist(), horizon=horizon, period=period,
        mode=asdict(mode), engine=engine, hill=hill_km, escape_window=escape_window))
    state, t = initial.ravel().copy(), 0.0
    tracker = EscapeTracker(hill_km, escape_window) if hill_km is not None else None
    if resume:
        if resume.get('terminal_event') is not None:
            raise ValueError('Нельзя продолжать терминальное событие')
        if resume['contract_sha256'] != contract:
            raise ValueError('Checkpoint относится к другому контракту')
        state, t = np.asarray(resume['state']), float(resume['time'])
        if state.shape != initial.ravel().shape or not np.isfinite(state).all() or not 0 <= t < horizon:
            raise ValueError('Некорректное состояние checkpoint')
        tracker = EscapeTracker(**resume['escape']) if resume['escape'] else None
    if engine == 'hierarchical_cached':
        cache = cache or MassiveCache(initial, gms, figures, epsilon=mode.cache_epsilon)
        expected = MassiveCache(initial, gms, figures, epsilon=mode.cache_epsilon,
                                max_step=cache.max_step).identity
        if cache.identity != expected or (resume and resume['cache_identity'] != cache.identity):
            raise ValueError('Кеш не соответствует checkpoint/физике')
    started, cpu = time.perf_counter(), time.process_time()
    cache_start = (cache.wall_seconds, cache.cpu_seconds) if cache else (0.0, 0.0)
    steps, nfev, drift = 0, 0, 0.0
    energy_function = massive_energy if engine == 'dop853_original' else (
        lambda y, gm, fg: fast_forces.energy(y, gm, fs))
    energy0 = energy_function(initial.ravel(), gms, figures)
    samples = []
    times = np.asarray(times if times is not None else np.linspace(0, horizon, 65), dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times) < 0) or np.any(times < 0) or np.any(times > horizon):
        raise ValueError('Неверная сетка диагностик')
    pending = list(times[times >= t])
    while pending and pending[0] == t:
        samples.append(dict(time=t, state=state.tolist()))
        pending.pop(0)
    event, error, status = None, None, 'completed'
    endpoint_residual = 0.0
    native, solver = None, None

    def checked_rhs(ti, y):
        budget()
        if engine == 'dop853_original':
            return relative_rhs(y, gms, figures)
        if engine == 'hierarchical_cached':
            massive = cache(ti).reshape(-1, 6)
            return np.r_[y[3:], fast_forces.probe_acceleration(y, massive[:, :3], gms, fs)]
        return fast_forces.rhs(y, gms, fs)

    def checkpoint():
        return dict(schema_version='W2-state-1', contract_sha256=contract, engine=engine,
            time=float(t), state=state.tolist(),
            cache_identity=cache.identity if cache else None,
            escape=tracker.snapshot() if tracker else None,
            terminal_event=event,
            continuation='physical_state_new_adaptive_history')

    try:
        budget()
        if cache:
            cache.extend(horizon, budget)
        if engine == 'ias15_reboundx':
            native = NativeIAS15(state, gms, figures, epsilon=mode.epsilon, t0=t,
                                 initial_dt=min(period*1e-5, horizon-t))
        while t < horizon:
            budget()
            left = t
            if native:
                dense = native.step(horizon)
                right = dense.right
                endpoint_residual = max(endpoint_residual, native.last_endpoint_residual)
            else:
                bound = min(horizon, cache.segment_at(t).right) if cache else horizon
                if solver is None or solver.status == 'finished':
                    y = state[-6:] if cache else state
                    atol = np.tile([mode.atol_position]*3+[mode.atol_velocity]*3, len(y)//6)
                    solver = DOP853(checked_rhs, t, y, bound, rtol=mode.rtol, atol=atol,
                                    max_step=period*mode.step_fraction)
                    previous_nfev = 0
                solver.step()
                nfev += solver.nfev-previous_nfev
                previous_nfev = solver.nfev
                if solver.status == 'failed' or not np.isfinite(solver.y).all():
                    raise ArithmeticError('DOP853 не завершил шаг')
                dense = solver.dense_output()
                # dense_output вызывает дополнительные вычисления силы.
                nfev += solver.nfev-previous_nfev
                previous_nfev = solver.nfev
                right = float(solver.t)
                if cache:
                    dense = CombinedSegment(cache.segment_at(left), dense)
            steps += 1
            # Выборки dense на принятом шаге готовятся один раз и общие для
            # контакта и ухода (W2-T008); при контакте внутри шага уход
            # проверяется на укороченном интервале с собственными выборками.
            step_samples = chebyshev_samples(dense, left, right) if right > left else None
            event = polynomial_contact(dense, left, right, len(gms), radii, samples=step_samples)
            end = event['time'] if event else right
            if tracker is not None and end > left:
                departure = tracker.advance(dense, left, end,
                                            samples=step_samples if end == right else None)
                if departure and (event is None or departure['time'] < event['time']):
                    event, end = departure, departure['time']
            selected = np.asarray(dense(end))
            if not np.isfinite(selected).all():
                raise ArithmeticError('Неконечное принятое состояние')
            state, t = selected.copy(), float(end)
            drift = max(drift, abs(energy_function(state, gms, figures)-energy0)/max(abs(energy0), 1e-300))
            while pending and pending[0] <= t:
                when = pending.pop(0)
                samples.append(dict(time=float(when), state=np.asarray(dense(when)).tolist()))
            if progress:
                progress(checkpoint())
            if event:
                break
    except TimeoutError as exc:
        status, error = 'partial', str(exc)
    except (ArithmeticError, ValueError, RuntimeError, MemoryError) as exc:
        status, error = 'failed', str(exc)
    if status != 'completed':
        outcome = 'unresolved'
    elif event:
        outcome = ('operational_escape' if event['event'] == 'operational_escape'
                   else 'host_contact' if event['body_index'] == 0 else 'other_contact')
    else:
        outcome = tracker.outcome() if tracker else 'survived'
    return dict(engine=engine, run_status=status, physical_outcome=outcome, error=error,
        last_valid_time=float(t), final_state=state.tolist(), event=event, samples=samples,
        checkpoint=checkpoint(), massive_energy_relative_drift=float(drift),
        endpoint_residual=endpoint_residual if native else None, steps=steps,
        nfev=None if native else nfev, wall_seconds=time.perf_counter()-started,
        cpu_seconds=time.process_time()-cpu,
        cache_preparation_wall_seconds=cache.wall_seconds-cache_start[0] if cache else 0.0,
        cache_preparation_cpu_seconds=cache.cpu_seconds-cache_start[1] if cache else 0.0,
        cache_bytes=cache.bytes if cache else 0,
        physical_surface_verified=False, permanent_escape_assessed=False,
        production_allowed=False)
