"""Ограниченный базовый runner DOP853; dense-события, checkpoint и измеренная стоимость."""

import time
import numpy as np
from scipy.integrate import DOP853
from submoon_research.dynamics.nominal import relative_rhs, massive_energy
from submoon_research.events.dense_contact import polynomial_contact
from submoon_research.events.reference import specific_energy


def integrate(
    initial,
    gms,
    figures,
    radii,
    horizon,
    *,
    rtol,
    atol_position,
    atol_velocity,
    max_step,
    budget=lambda: None,
    t0=0.0,
    checkpoint_time=None,
    checkpoint=None,
):
    state = np.asarray(initial, dtype=float).ravel()
    if not 0 <= t0 < horizon or max_step <= 0 or not np.isfinite(state).all():
        raise ValueError("Неверный базовый протокол интеграции")
    count = len(gms)
    started = time.perf_counter()
    cpu = time.process_time()
    atol = np.tile([atol_position] * 3 + [atol_velocity] * 3, count + 1)

    def rhs(t, y):
        budget()
        return relative_rhs(y, gms, figures)

    bound = checkpoint_time if checkpoint_time and t0 < checkpoint_time < horizon else horizon
    solver = DOP853(rhs, t0, state, bound, rtol=rtol, atol=atol, max_step=max_step)
    event = None
    samples = []
    nfev = 0
    steps = 0
    saved = None
    initial_energy = massive_energy(state, gms, figures)
    energy_drift = 0.0
    min_distance = np.inf
    crossing_times = []
    last_positive = specific_energy(state[-6:], gms[0]) > 0
    checkpoints = 0
    while True:
        while solver.status == "running":
            budget()
            left = solver.t
            solver.step()
            steps += 1
            if solver.status == "failed" or not np.isfinite(solver.y).all():
                raise RuntimeError("DOP853 не завершил допустимый шаг")
            dense = solver.dense_output()
            event = polynomial_contact(dense, left, solver.t, count, radii)
            selected = dense(event["time"]) if event else solver.y
            sample_time = event["time"] if event else solver.t
            drift = abs(massive_energy(selected, gms, figures) - initial_energy) / max(
                abs(initial_energy), 1e-300
            )
            energy_drift = max(energy_drift, float(drift))
            min_distance = min(min_distance, float(np.linalg.norm(selected[-6:-3])))
            positive = specific_energy(selected[-6:], gms[0]) > 0
            if positive != last_positive:
                crossing_times.append(dict(time=float(sample_time), positive=bool(positive)))
            last_positive = positive
            if len(samples) < 256 or steps % 20 == 0 or event or solver.status == "finished":
                samples.append(dict(time=float(sample_time), probe_state=selected[-6:].tolist()))
            if event:
                solver.y = selected
                solver.t = sample_time
                break
        nfev += solver.nfev
        if event or solver.t >= horizon:
            break
        saved = dict(
            schema_version="0.1",
            time=float(solver.t),
            state=solver.y.tolist(),
            horizon_seconds=float(horizon),
            rtol=rtol,
            atol_position=atol_position,
            atol_velocity=atol_velocity,
            max_step=max_step,
            continuation="restart_from_exact_state_new_adaptive_history",
            gms=list(map(float, gms)),
        )
        if checkpoint:
            checkpoint(saved)
        checkpoints += 1
        solver = DOP853(rhs, solver.t, solver.y, horizon, rtol=rtol, atol=atol, max_step=max_step)
    return dict(
        last_valid_time=float(solver.t),
        final_state=solver.y.tolist(),
        event=event,
        physical_outcome="host_contact"
        if event and event["body_index"] == 0
        else "other_contact"
        if event
        else "unresolved" if last_positive else "survived",
        samples=samples,
        energy_crossings=crossing_times,
        minimum_recorded_radius_km=min_distance,
        massive_energy_relative_drift=energy_drift,
        checkpoint=saved,
        checkpoint_count=checkpoints,
        nfev=nfev,
        steps=steps,
        wall_seconds=time.perf_counter() - started,
        cpu_seconds=time.process_time() - cpu,
        physical_surface_verified=False,
        permanent_escape_assessed=False,
    )
