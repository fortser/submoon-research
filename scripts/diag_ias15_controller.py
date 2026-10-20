"""W2-T037: read-only IAS15 buffers on an exactly checked short failure replay."""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

from replay_ias15_snapshot import b_fingerprint, step_record  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, canonical, file_digest, output_path, read_json,
)
from submoon_research.diagnostics.ias15_controller import geometry, reconstruct_prs23  # noqa: E402
from submoon_research.diagnostics.ias15_replay import environment, guard_reason, load_snapshot  # noqa: E402

SNAPSHOT = ROOT/'results/evidence/W2_B_capture_20261019/attempt/diagnostic_artifacts/pre_step_snapshot/snapshot_manifest.json'
SNAPSHOT_SHA = '9a634be5f9404ed88ccf66839aedc7133f4b38a27520e9d029e31dd78557b52a'
REFERENCE = ROOT/'results/evidence/W2_B_replay_20261019/native_replay.json'
REFERENCE_SHA = 'fd3d85242b86559780fe433d326e7b462d474fbd81d18518049d3a14de71af12'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', required=True)
    p.add_argument('--wall-seconds', type=float, default=60.)
    args = p.parse_args(argv)
    if not 0 < args.wall_seconds <= 60:
        raise ValueError('Лимит пассивного replay <=60 s')
    out = output_path(ROOT/args.output, SNAPSHOT.parent)
    output_path(out, REFERENCE.parent)
    output_path(out, ROOT/'runs/W2-P2-2a-56220810')
    if file_digest(REFERENCE) != REFERENCE_SHA:
        raise ValueError('Эталон replay изменён')
    reference = read_json(REFERENCE)
    native, _ = load_snapshot(SNAPSHOT, SNAPSHOT_SHA, expected_identity=reference['identity'])
    if (reference['identity']['B_implementation'] != b_fingerprint()
            or str(native.sim.integrator.adaptive_mode) != 'PRS23'):
        raise ValueError('Реализация/режим не соответствует постановке')
    fields = {f.name.decode(): f.element_size for f in native.sim.integrator.callbacks.field_descriptor_list}
    if any(fields.get(k) != size for k, size in [('a0', 8), ('br', 56), ('csx', 8), ('csv', 8)]):
        raise ValueError('Не соответствует ABI буферов')
    started = time.monotonic()
    records, mismatches, failure = [], [], None
    budget_stop = False
    for index in range(417):
        if time.monotonic()-started >= args.wall_seconds:
            budget_stop = True
            break
        left = float(native.sim.t)
        proposed = float(native.sim.dt)
        start_state = native.absolute_state()
        reference_match = False
        try:
            step = step_record(native, reference['identity'])
            reference_match = (index < len(reference['records'])
                               and canonical(step) == canonical(reference['records'][index]))
        except (ArithmeticError, ValueError, RuntimeError) as exc:
            failure = dict(reason=guard_reason(exc, left, float(native.sim.t),
                               reference['identity']['years']*365.25*86400.),
                           error=str(exc), left=left, right=float(native.sim.t),
                           dt_done=float(native.sim.dt_last_done), next_dt=float(native.sim.dt))
            reference_match = index == 416 and canonical(failure) == canonical(reference['failure'])
        if not reference_match:
            mismatches.append(index)
        dt = float(native.sim.dt_last_done)
        a0 = native._array('a0', 3*native.count).reshape(native.count, 3)
        br = native._array('br', 21*native.count).reshape(7, native.count, 3)
        controller = reconstruct_prs23(a0, br, dt, float(native.sim.integrator.epsilon),
                                      float(native.sim.integrator.min_dt))
        next_dt = float(native.sim.dt)
        relative_error = abs(controller['predicted_next_dt']-next_dt)/abs(next_dt)
        record = dict(index=index, left=left, right=float(native.sim.t), proposed_dt=proposed,
                      dt_done=dt, next_dt=next_dt, time_ulp=math.ulp(left),
                      controller_relative_error=relative_error, controller=controller,
                      geometry=geometry(start_state, dt, reference['identity']['gm_host']),
                      a0=a0.tolist(), br=br.tolist(),
                      csx=native._array('csx', 3*native.count).reshape(native.count, 3).tolist(),
                      csv=native._array('csv', 3*native.count).reshape(native.count, 3).tolist(),
                      iterations_max_exceeded=int(native.sim.integrator.iterations_max_exceeded),
                      reference_match=reference_match, failure=failure)
        records.append(record)
        if failure:
            break
    checks = dict(complete=len(records) == 417 and not budget_stop,
                  passive_exact=not mismatches and len(records) == 417,
                  expected_guard=canonical(failure) == canonical(reference['failure']),
                  controller_reconstruction=bool(records) and all(
                      r['controller_relative_error'] <= 1e-12 for r in records),
                  no_internal_rejection_predicted=all(
                      not r['controller']['rejection_predicted'] for r in records),
                  unchanged_reference=file_digest(REFERENCE) == REFERENCE_SHA,
                  unchanged_snapshot=file_digest(SNAPSHOT) == SNAPSHOT_SHA)
    summary = dict(checks=checks, passed=all(checks.values()),
                   mismatch_indices=mismatches, steps=len(records), failure=failure,
                   wall_seconds=time.monotonic()-started,
                   max_controller_relative_error=max((r['controller_relative_error'] for r in records), default=None))
    report = dict(run_id='W2-B-controller-20261019', task='W2-T037',
                  identity=reference['identity'], environment=environment(),
                  snapshot_sha256=SNAPSHOT_SHA, reference_sha256=REFERENCE_SHA,
                  code_sha256={path.relative_to(ROOT).as_posix(): file_digest(path) for path in (
                      Path(__file__), ROOT/'src/submoon_research/diagnostics/ias15_controller.py',
                      ROOT/'scripts/replay_ias15_snapshot.py',
                      ROOT/'data/raw/rebound_5_1_1_20261019/integrator_ias15.c')},
                  summary=summary, records=records, scientific_admission=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(out, report)
    print(summary)
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
