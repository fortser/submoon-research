"""W2-P011 bounded matrix. Prepare all inputs, accept N, then run each P once."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import replay_ias15_snapshot as replay  # noqa: E402
from diag_ias15_controller import REFERENCE, REFERENCE_SHA, SNAPSHOT, SNAPSHOT_SHA  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, canonical, file_digest, output_path, read_json,
)
from submoon_research.diagnostics.ias15_frame_controls import (  # noqa: E402
    VARIANTS, common_grid, run_physical, transform,
)
from submoon_research.diagnostics.ias15_replay import environment, load_snapshot  # noqa: E402


def code_hashes():
    files = [Path(__file__), ROOT/'scripts/replay_ias15_snapshot.py', ROOT/'scripts/diag_ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_frame_controls.py',
             ROOT/'src/submoon_research/diagnostics/ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_replay.py',
             ROOT/'src/submoon_research/contracts/p2_files.py',
             ROOT/'docs/tasks/W2_B_discriminating_controls_v1.md',
             ROOT/'docs/tasks/W2_B_frames_execution_20261019.md']
    files += list((ROOT/'src/submoon_research/dynamics').glob('*.py'))
    return {p.relative_to(ROOT).as_posix(): file_digest(p) for p in files}


def prepare(directory):
    if file_digest(REFERENCE) != REFERENCE_SHA:
        raise ValueError('Reference changed')
    reference = read_json(REFERENCE)
    if reference['identity']['B_implementation'] != replay.b_fingerprint():
        raise ValueError('B implementation changed')
    _, manifest = load_snapshot(SNAPSHOT, SNAPSHOT_SHA, expected_identity=reference['identity'])
    physical = read_json(SNAPSHOT.parent/'physical.json')
    variants = {v: transform(physical, v) for v in VARIANTS}
    grid = common_grid(physical['time'], reference['failure']['left'])
    # All geometry checks above precede creation of executable matrix inputs.
    directory.mkdir(parents=True, exist_ok=False)
    inputs = dict(run_id='W2-B-frames-20261019', task='W2-T038', snapshot_sha256=SNAPSHOT_SHA,
                  reference_sha256=REFERENCE_SHA, identity=reference['identity'],
                  environment=manifest['environment'], variants=variants, grid=grid,
                  code_sha256=code_hashes(), physical_source_sha256=file_digest(SNAPSHOT.parent/'physical.json'),
                  max_steps=10000, wall_seconds=60., scientific_admission=False)
    atomic_json(directory/'inputs.json', inputs)
    print(file_digest(directory/'inputs.json'))
    return 0


def verify_baseline(directory, reference):
    data = read_json(directory/'N.json')
    checks = dict(native=data['restart_kind'] == 'native', requested_steps=data['requested_steps'] == 417,
                  identity=data['identity'] == reference['identity'], snapshot=data['snapshot_sha256'] == SNAPSHOT_SHA,
                  records_exact=canonical(data['records']) == canonical(reference['records']),
                  failure_exact=canonical(data['failure']) == canonical(reference['failure']))
    if not all(checks.values()):
        raise ValueError('Native baseline mismatch: '+repr(checks))
    return checks


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', required=True)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--variant', choices=('N', *VARIANTS))
    p.add_argument('--inputs-sha256')
    args = p.parse_args(argv)
    directory = (ROOT/args.directory).resolve()
    candidate = directory/('inputs.json' if args.prepare or not args.variant else args.variant+'.json')
    output_path(candidate, SNAPSHOT.parent)
    output_path(candidate, REFERENCE.parent)
    output_path(candidate, ROOT/'runs/W2-P2-2a-56220810')
    if args.prepare:
        if args.variant or args.inputs_sha256:
            raise ValueError('Prepare must be separate from execution')
        return prepare(directory)
    if not args.variant or not args.inputs_sha256 or file_digest(directory/'inputs.json') != args.inputs_sha256:
        raise ValueError('Execution requires pinned inputs SHA')
    inputs = read_json(directory/'inputs.json')
    if (inputs['code_sha256'] != code_hashes() or inputs['environment'] != environment()
            or file_digest(REFERENCE) != REFERENCE_SHA or file_digest(SNAPSHOT) != SNAPSHOT_SHA
            or inputs['identity']['B_implementation'] != replay.b_fingerprint()):
        raise ValueError('Inputs/environment/code identity changed')
    reference = read_json(REFERENCE)
    output = output_path(directory/(args.variant+'.json'), SNAPSHOT.parent)
    # Exclusive reservation: no automatic rerun even if a worker later times out.
    with (directory/(args.variant+'.started')).open('x', encoding='utf-8') as handle:
        handle.write(args.inputs_sha256+'\n')
    if args.variant == 'N':
        code = replay.main(['--snapshot', str(SNAPSHOT), '--snapshot-sha256', SNAPSHOT_SHA,
                           '--job-fingerprint', inputs['identity']['job_fingerprint'], '--steps', '417',
                           '--wall-seconds', '60', '--output', str(output)])
        checks = verify_baseline(directory, reference)
        if code != 1:
            raise ValueError('Expected numerical failure exit code 1')
        atomic_json(directory/'baseline_acceptance.json', dict(checks=checks, passed=True,
                    baseline_sha256=file_digest(output), source_sha256=REFERENCE_SHA))
        return 0
    verify_baseline(directory, reference)
    result = run_physical(inputs['variants'][args.variant], inputs['identity'], inputs['grid'])
    result.update(run_id=inputs['run_id']+'-'+args.variant, inputs_sha256=args.inputs_sha256,
                  code_sha256=inputs['code_sha256'], environment=environment())
    atomic_json(output, result)
    print({k: result[k] for k in ('variant', 'status', 'attempts', 'last_valid_elapsed', 'wall_seconds')})
    # guard/partial are data, not acceptance of a successful trajectory.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
