"""Продолжение только той же номинальной задачи и численного режима."""

import hashlib
import json
import numpy as np


def validate_checkpoint(cp, *, initial, orbit_id, model_sha256, gms, horizon, options):
    if (
        cp["schema_version"] != "0.1"
        or cp["continuation"] != "restart_from_exact_state_new_adaptive_history"
    ):
        raise ValueError("Неподдерживаемый checkpoint")
    expected = dict(
        model_sha256=model_sha256,
        orbit_id=orbit_id,
        input_state_sha256=hashlib.sha256(json.dumps(initial).encode()).hexdigest(),
        gms=list(map(float, gms)),
        horizon_seconds=horizon,
    )
    expected.update({k: options[k] for k in ("rtol", "atol_position", "atol_velocity", "max_step")})
    if any(cp[k] != v for k, v in expected.items()):
        raise ValueError("Checkpoint относится к другой постановке или численному режиму")
    state = np.asarray(cp["state"], dtype=float)
    if (
        state.shape != (6 * (len(gms) + 1),)
        or not np.isfinite(state).all()
        or not 0 < cp["time"] < horizon
    ):
        raise ValueError("Неверное сохранённое состояние или время")
    return cp
