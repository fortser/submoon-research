"""Small, explicitly synthetic installation and provenance check, not a W2 release."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata as metadata
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml

from submoon_research.tracking import atomic_text, utc_now

CORE = {
    "numpy": "numpy", "astropy": "astropy", "scipy": "scipy", "sympy": "sympy",
    "rebound": "rebound", "matplotlib": "matplotlib", "pandas": "pandas",
    "pyarrow": "pyarrow", "PyYAML": "yaml", "pydantic": "pydantic",
    "pytest": "pytest", "ruff": "ruff",
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def environment(check_imports=False):
    versions, failures = {}, {}
    for name, module in CORE.items():
        try:
            versions[name] = metadata.version(name)
            if check_imports:
                importlib.import_module(module)
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
    return {"python": sys.version, "platform": platform.platform(),
            "logical_cpus": os.cpu_count(), "packages": versions, "failures": failures}


def validate_smoke_config(config):
    expected = {"schema_version", "experiment_id", "status", "data_kind", "physics_level", "units",
                "gm", "radius", "periods", "samples", "integrator", "control_integrator",
                "rtol", "atol", "tolerances"}
    if set(config) != expected:
        raise ValueError("Unexpected/missing smoke configuration fields")
    fixed = {"status": "ready", "data_kind": "synthetic", "physics_level": "L0",
             "units": "dimensionless", "integrator": "ias15", "control_integrator": "DOP853"}
    if any(config[key] != value for key, value in fixed.items()):
        raise ValueError("Only the synthetic dimensionless L0 IAS15/DOP853 smoke is implemented")
    for key in ["gm", "radius", "periods", "rtol", "atol"]:
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid positive finite value: {key}")
    if type(config["samples"]) is not int or not 3 <= config["samples"] <= 4096:
        raise ValueError("samples must be an integer in [3, 4096]")
    if config["periods"] > 100:
        raise ValueError("Smoke is limited to 100 periods")
    keys = {"position_max", "relative_energy_max", "relative_angular_momentum_max", "independent_position_max"}
    if set(config["tolerances"]) != keys:
        raise ValueError("Invalid tolerance fields")
    if any(not isinstance(v, (float, int)) or isinstance(v, bool) or not math.isfinite(v) or v <= 0
           for v in config["tolerances"].values()):
        raise ValueError("Tolerances must be positive and finite")


def run_smoke(root):
    # Configure library thread limits before numerical imports.
    for key in ["OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"]:
        os.environ[key] = "1"
    import numpy as np
    import pandas as pd
    import rebound
    from astropy.table import Table
    from scipy.integrate import solve_ivp

    config_path = root / "configs/experiments/L0_smoke.yaml"
    resource_path = root / "configs/resources.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    resources = yaml.safe_load(resource_path.read_text(encoding="utf-8"))
    validate_smoke_config(config)
    if shutil.disk_usage(root).free < resources["minimum_free_disk_gib"] * 2**30:
        raise ValueError("Insufficient free disk for the configured reserve")
    inputs = {p.relative_to(root).as_posix(): sha256(p) for p in [config_path, resource_path]}
    source_root = Path(__file__).resolve().parents[1]
    code = {p.relative_to(source_root).as_posix(): sha256(p) for p in sorted(source_root.rglob("*.py"))}
    env = environment()
    science = {"config": config, "inputs": inputs, "code": code, "environment": env}
    scientific_id = hashlib.sha256(json.dumps(science, sort_keys=True).encode()).hexdigest()
    run_id = "L0-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    folder = root / "runs" / run_id
    folder.mkdir(parents=True, exist_ok=False)
    for name in ["initial_conditions", "results", "logs", "checkpoints"]:
        (folder / name).mkdir()
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False)
    git_status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=False)
    manifest = {"schema_version": "0.1", "run_id": run_id, "experiment_id": config["experiment_id"],
                "scientific_id": scientific_id, "data_kind": "synthetic", "status": "running",
                "started_utc": utc_now(), "finished_utc": None, "inputs_sha256": inputs,
                "code_sha256": code, "environment": env, "git_commit": git.stdout.strip() if git.returncode == 0 else None,
                "git_dirty": bool(git_status.stdout.strip()) if git_status.returncode == 0 else None,
                "resources": resources, "artifacts_sha256": {}}
    write_json(folder / "manifest.json", manifest)
    atomic_text(folder / "config.resolved.yaml", yaml.safe_dump(config, sort_keys=False))
    start_wall, start_cpu = time.perf_counter(), time.process_time()
    try:
        gm, radius = config["gm"], config["radius"]
        speed = math.sqrt(gm / radius)
        frequency = math.sqrt(gm / radius**3)
        times = np.linspace(0, config["periods"] * 2 * np.pi / frequency, config["samples"])
        initial = [radius, 0.0, 0.0, 0.0, speed, 0.0]
        write_json(folder / "initial_conditions/state.json", {
            "data_kind": "synthetic", "origin": "fixed_central_mass", "frame": "inertial_fixed_axes",
            "epoch": 0.0, "time_scale": "dimensionless", "state_xyz_vxyz": initial,
            "measure": "single_deterministic_control", "weight": 1.0, "seed": None})
        sim = rebound.Simulation()
        sim.G = 1.0
        sim.add(m=gm)
        sim.add(m=0.0, x=radius, vy=speed)
        sim.integrator = "ias15"
        states = []
        for t in times:
            if time.perf_counter() - start_wall > resources["smoke_wall_seconds"]:
                raise TimeoutError("Smoke wall budget exceeded between integration steps")
            sim.integrate(float(t))
            p = sim.particles[1]
            states.append([p.x, p.y, p.z, p.vx, p.vy, p.vz])
        states = np.array(states)

        def rhs(t, state):
            if time.perf_counter() - start_wall > resources["smoke_wall_seconds"]:
                raise TimeoutError("Smoke wall budget exceeded in independent control")
            return np.r_[state[3:], -gm * state[:3] / np.linalg.norm(state[:3])**3]

        control = solve_ivp(rhs, (0, times[-1]), initial, method="DOP853", t_eval=times,
                            rtol=config["rtol"], atol=config["atol"])
        if not control.success or control.y.shape != states.T.shape:
            raise RuntimeError("Independent integration did not reach all requested times")
        analytic = np.column_stack([radius * np.cos(frequency * times),
                                    radius * np.sin(frequency * times), np.zeros_like(times)])
        energy = np.sum(states[:, 3:]**2, axis=1) / 2 - gm / np.linalg.norm(states[:, :3], axis=1)
        angular = np.linalg.norm(np.cross(states[:, :3], states[:, 3:]), axis=1)
        metrics = {"position_max": float(np.max(np.linalg.norm(states[:, :3] - analytic, axis=1))),
                   "relative_energy_max": float(np.max(np.abs(energy / (-gm / (2 * radius)) - 1))),
                   "relative_angular_momentum_max": float(np.max(np.abs(angular / (radius * speed) - 1))),
                   "independent_position_max": float(np.max(np.linalg.norm(states[:, :3] - control.y[:3].T, axis=1)))}
        checks = {name: value <= config["tolerances"][name] for name, value in metrics.items()}
        values = np.column_stack([times, states])
        columns = ["t", "x", "y", "z", "vx", "vy", "vz"]
        table = Table(values, names=columns, meta={"data_kind": "synthetic", "units": "dimensionless", "run_id": run_id})
        ecsv = folder / "results/orbit.ecsv"
        parquet = folder / "results/orbit.parquet"
        table.write(ecsv, format="ascii.ecsv")
        pd.DataFrame(values, columns=columns).to_parquet(parquet, index=False)
        checks["ecsv_roundtrip"] = bool(np.allclose(np.column_stack([Table.read(ecsv)[c] for c in columns]), values, rtol=0, atol=0))
        checks["parquet_roundtrip"] = bool(np.array_equal(pd.read_parquet(parquet).to_numpy(), values))
        write_json(folder / "results/units.json", {c: "dimensionless" for c in columns})
        write_json(folder / "validation.json", {"status": "passed" if all(checks.values()) else "failed",
            "metrics": metrics, "tolerances": config["tolerances"], "checks": checks,
            "limitations": ["Synthetic two-body circular orbit only", "No event, ensemble or checkpoint validation",
                            "Not a W2 completion or a physical host result"],
            "integrator_parameters": {"ias15": "installed-library defaults", "DOP853": {"rtol": config["rtol"], "atol": config["atol"]}}})
        atomic_text(folder / "logs/execution.log", f"{utc_now()} completed L0; validation={all(checks.values())}\n")
        manifest["status"] = "completed"
        manifest["validation_status"] = "passed" if all(checks.values()) else "failed"
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        if size > resources["smoke_output_mib"] * 2**20:
            raise RuntimeError("Smoke output budget exceeded")
    except BaseException as exc:
        manifest["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        atomic_text(folder / "logs/execution.log", f"{utc_now()} {manifest['error']}\n")
        raise
    finally:
        manifest["finished_utc"] = utc_now()
        manifest["wall_seconds"] = time.perf_counter() - start_wall
        manifest["cpu_seconds"] = time.process_time() - start_cpu
        manifest["artifacts_sha256"] = {p.relative_to(folder).as_posix(): sha256(p)
                                         for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "manifest.json"}
        write_json(folder / "manifest.json", manifest)
    return folder, manifest["validation_status"] == "passed"
