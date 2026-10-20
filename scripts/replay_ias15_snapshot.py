"""Короткий native/physical replay IAS15 в отдельном процессе; не runner событий."""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

from admission_p2_horizons import b_fingerprint  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, digest, file_digest, output_path, read_json,
)
from submoon_research.diagnostics.ias15_replay import guard_reason, load_snapshot  # noqa: E402
from submoon_research.dynamics.engine_compare import ias15_step_limit  # noqa: E402


def step_record(native, identity):
    bound = identity['years']*365.25*86400.0
    left = float(native.sim.t)
    maximum = ias15_step_limit(native, identity['period_seconds'], identity['gm_host'], identity['step_fraction'])
    segment = native.step(bound, maximum)
    return dict(left=left, right=float(native.sim.t), dt_done=float(native.sim.dt_last_done),
                next_dt=float(native.sim.dt), absolute_state=native.absolute_state().tolist(),
                coefficients_sha256=digest(segment.coefficients.tolist()),
                endpoint_residual=native.last_endpoint_residual)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot', required=True)
    p.add_argument('--snapshot-sha256', required=True)
    p.add_argument('--job-fingerprint', required=True)
    p.add_argument('--steps', type=int, default=64)
    p.add_argument('--wall-seconds', type=float, default=60.0)
    p.add_argument('--physical-only', action='store_true')
    p.add_argument('--output', required=True)
    args = p.parse_args(argv)
    path = (ROOT/args.snapshot).resolve()
    if not 1 <= args.steps <= 1024 or not 0 < args.wall_seconds <= 600:
        raise ValueError('Короткий replay: steps<=1024, wall<=600 s')
    if file_digest(path) != args.snapshot_sha256:
        raise ValueError('Неверный внешний SHA snapshot')
    manifest = read_json(path)
    identity = manifest['identity']
    if identity['job_fingerprint'] != args.job_fingerprint or identity['B_implementation'] != b_fingerprint():
        raise ValueError('Чужой контракт replay или реализация B')
    out = output_path(ROOT/args.output, path.parent)
    if identity.get('source_run'):
        output_path(out, ROOT/identity['source_run'])
    if identity.get('source_inputs_manifest'):
        output_path(out, (ROOT/identity['source_inputs_manifest']).resolve().parent)
    native, _ = load_snapshot(path, args.snapshot_sha256, expected_identity=identity,
                              physical_only=args.physical_only)
    records, failure = [], None
    started = time.monotonic()
    for _ in range(args.steps):
        if time.monotonic()-started > args.wall_seconds:
            failure = dict(reason='diagnostic_budget')
            break
        if native.sim.t >= identity['years']*365.25*86400.0:
            break
        left = float(native.sim.t)
        try:
            records.append(step_record(native, identity))
        except (ArithmeticError, ValueError, RuntimeError) as exc:
            failure = dict(reason=guard_reason(exc, left, float(native.sim.t), identity['years']*365.25*86400.0),
                           error=str(exc), left=left, right=float(native.sim.t),
                           dt_done=float(native.sim.dt_last_done), next_dt=float(native.sim.dt))
            break
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(out, dict(snapshot_sha256=args.snapshot_sha256, identity=identity, records=records,
                         failure=failure, requested_steps=args.steps,
                         restart_kind='physical_state_new_adaptive_history' if args.physical_only else 'native',
                         wall_seconds=time.monotonic()-started,
                         scope='native steps only; no event tracker or production admission'))
    print(out, 'failure=', failure)
    return 1 if failure else 0


if __name__ == '__main__':
    raise SystemExit(main())
