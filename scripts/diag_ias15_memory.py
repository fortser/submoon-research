"""W2-P012: prepare inputs, accept M0 against C073, run MP/MC/MD/MA once, analyze.

Each variant is a separate process (see run_ias15_memory_controls.py). No reruns:
an exclusive <variant>.started marker is created before integration.
"""
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
    atomic_json, digest, file_digest, output_path, read_json,
)
from submoon_research.diagnostics.ias15_frame_controls import compare_samples  # noqa: E402
from submoon_research.diagnostics.ias15_memory_controls import (  # noqa: E402
    MASKS, VARIANTS, apply_mask, first_divergence, integrate_window, verify_m0, verify_ma,
)
from submoon_research.diagnostics.ias15_replay import environment, load_snapshot  # noqa: E402

RUN_ID = 'W2-B-memory-20261020'
FRAMES = ROOT/'results/evidence/W2_B_frames_20261019'
FRAMES_INPUTS_SHA = '243acb30b4e5882911f10da99553ad10ff9052c421e720d70c0b266673b5a083'
P00_SHA = 'fa5d61d86d989b45b45a59806c1366f3393d65e0b6199be5f43ac0fc25e7823c'
PROTECTED = (SNAPSHOT.parent, REFERENCE.parent, FRAMES, ROOT/'runs/W2-P2-2a-56220810')


def code_hashes():
    files = [Path(__file__), ROOT/'scripts/run_ias15_memory_controls.py',
             ROOT/'scripts/replay_ias15_snapshot.py', ROOT/'scripts/diag_ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_memory_controls.py',
             ROOT/'src/submoon_research/diagnostics/ias15_frame_controls.py',
             ROOT/'src/submoon_research/diagnostics/ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_replay.py',
             ROOT/'src/submoon_research/contracts/p2_files.py',
             ROOT/'docs/tasks/W2_B_memory_controls_v1.md']
    files += list((ROOT/'src/submoon_research/dynamics').glob('*.py'))
    return {p.relative_to(ROOT).as_posix(): file_digest(p) for p in sorted(files)}


def _new(path):
    for protected in PROTECTED:
        output_path(path, protected)
    return path


def prepare(directory):
    if file_digest(REFERENCE) != REFERENCE_SHA or file_digest(SNAPSHOT) != SNAPSHOT_SHA:
        raise ValueError('Reference or snapshot changed')
    if (file_digest(FRAMES/'matrix/inputs.json') != FRAMES_INPUTS_SHA
            or file_digest(FRAMES/'matrix/P00.json') != P00_SHA):
        raise ValueError('P011 inputs or P00 changed')
    reference = read_json(REFERENCE)
    if reference['identity']['B_implementation'] != replay.b_fingerprint():
        raise ValueError('B implementation changed')
    _, manifest = load_snapshot(SNAPSHOT, SNAPSHOT_SHA, expected_identity=reference['identity'])
    frames = read_json(FRAMES/'matrix/inputs.json')
    scales = read_json(FRAMES/'comparison.json')
    physical = read_json(SNAPSHOT.parent/'physical.json')
    if frames['grid']['original_t0'] != physical['time']:
        raise ValueError('Grid origin differs from snapshot time')
    directory.mkdir(parents=True, exist_ok=False)
    inputs = dict(run_id=RUN_ID, task='W2-T041', plan='W2-P012', snapshot_sha256=SNAPSHOT_SHA,
                  reference_sha256=REFERENCE_SHA, frames_inputs_sha256=FRAMES_INPUTS_SHA,
                  p00_sha256=P00_SHA, identity=reference['identity'],
                  environment=manifest['environment'], grid=frames['grid'],
                  figures=physical['figures'], masks={k: dict(buffers=list(v['buffers']),
                                                              dt_last_done=v['dt_last_done'])
                                                      for k, v in MASKS.items()},
                  variant_fingerprints={v: digest(dict(run=RUN_ID, mask=MASKS[v], grid=frames['grid'],
                                                       snapshot=SNAPSHOT_SHA, identity=reference['identity']))
                                        for v in VARIANTS},
                  radius_scale_km=scales['radius_scale_km'], speed_scale_km_s=scales['speed_scale_km_s'],
                  code_sha256=code_hashes(), max_steps=10000, wall_seconds=60., scientific_admission=False)
    atomic_json(directory/'inputs.json', inputs)
    print(file_digest(directory/'inputs.json'))
    return 0


def _check_identity(directory, inputs_sha):
    if not inputs_sha or file_digest(directory/'inputs.json') != inputs_sha:
        raise ValueError('Execution requires pinned inputs SHA')
    inputs = read_json(directory/'inputs.json')
    if (inputs['code_sha256'] != code_hashes() or inputs['environment'] != environment()
            or file_digest(REFERENCE) != REFERENCE_SHA or file_digest(SNAPSHOT) != SNAPSHOT_SHA
            or file_digest(FRAMES/'matrix/P00.json') != P00_SHA
            or inputs['identity']['B_implementation'] != replay.b_fingerprint()):
        raise ValueError('Inputs/environment/code identity changed')
    return inputs


def run_variant(directory, variant, inputs_sha):
    inputs = _check_identity(directory, inputs_sha)
    if variant != 'M0':
        acceptance = directory/'m0_acceptance.json'
        if not acceptance.exists() or not read_json(acceptance)['passed']:
            raise ValueError('M0 not accepted; package stopped')
    output = _new(directory/(variant+'.json'))
    with (directory/(variant+'.started')).open('x', encoding='utf-8') as handle:
        handle.write(inputs_sha+'\n')
    native, _ = load_snapshot(SNAPSHOT, SNAPSHOT_SHA, expected_identity=inputs['identity'])
    mask = apply_mask(native, variant, inputs['figures'])
    result = integrate_window(native, inputs['identity'], inputs['grid'],
                              wall_seconds=inputs['wall_seconds'], max_steps=inputs['max_steps'])
    result.update(variant=variant, run_id=RUN_ID+'-'+variant, inputs_sha256=inputs_sha, mask=mask,
                  experimental_fingerprint=inputs['variant_fingerprints'][variant],
                  code_sha256=inputs['code_sha256'], environment=environment(),
                  restart_kind='native_snapshot_with_selective_reset')
    atomic_json(output, result)
    print({k: result[k] for k in ('variant', 'status', 'attempts', 'accepted_steps',
                                  'last_valid_elapsed', 'wall_seconds')})
    if variant == 'M0':
        verdict = verify_m0(result, read_json(REFERENCE))
        verdict.update(m0_sha256=file_digest(output), reference_sha256=REFERENCE_SHA)
        atomic_json(_new(directory/'m0_acceptance.json'), verdict)
        print('M0 acceptance', verdict['passed'], verdict['checks'])
        return 0 if verdict['passed'] else 2
    if variant == 'MA':
        verdict = verify_ma(result, read_json(FRAMES/'matrix/P00.json'))
        verdict.update(ma_sha256=file_digest(output), p00_sha256=P00_SHA)
        atomic_json(_new(directory/'ma_completeness.json'), verdict)
        print('MA completeness vs P00', verdict['passed'], verdict['checks'])
    # guard/partial/target are data; acceptance of a trajectory is not claimed.
    return 0


def analyze(directory, inputs_sha):
    inputs = _check_identity(directory, inputs_sha)
    results = {v: read_json(directory/(v+'.json')) for v in VARIANTS if (directory/(v+'.json')).exists()}
    p00 = read_json(FRAMES/'matrix/P00.json')
    scale = dict(radius_scale=inputs['radius_scale_km'], speed_scale=inputs['speed_scale_km_s'])
    summary = {}
    for v, r in results.items():
        counts = {}
        for rec in r['records']:
            counts[str(rec['limiting_body'])] = counts.get(str(rec['limiting_body']), 0)+1
        summary[v] = dict(status=r['status'], target_reached=r['target_reached'], attempts=r['attempts'],
                          accepted_steps=r['accepted_steps'], last_valid_elapsed=r['last_valid_elapsed'],
                          failure=r['failure'], dt_quantiles_s=r['dt_quantiles_s'],
                          limiting_body_counts=counts, wall_seconds=r['wall_seconds'],
                          first_divergence_vs_M0=None if v == 'M0' or 'M0' not in results
                          else first_divergence(r, results['M0']),
                          first_divergence_vs_P00=first_divergence(r, p00),
                          vs_P00=compare_samples(r, p00, **scale),
                          vs_M0=None if v == 'M0' or 'M0' not in results
                          else compare_samples(r, results['M0'], **scale),
                          probe_minus_host_csx_km=r['mask']['probe_minus_host_csx_km'])
    out = dict(run_id=RUN_ID, inputs_sha256=inputs_sha, variants=summary,
               m0_acceptance=read_json(directory/'m0_acceptance.json')
               if (directory/'m0_acceptance.json').exists() else None,
               ma_completeness=read_json(directory/'ma_completeness.json')
               if (directory/'ma_completeness.json').exists() else None,
               missing=[v for v in VARIANTS if v not in results],
               source_hashes={v+'.json': file_digest(directory/(v+'.json')) for v in results},
               scientific_admission=False,
               interpretation=('sensitivity of this snapshot to declared memory groups; '
                               'not a root cause, not a production fallback'))
    atomic_json(_new(directory/'comparison.json'), out)
    for v, s in summary.items():
        print(v, s['status'], 'attempts', s['attempts'], 'elapsed', s['last_valid_elapsed'],
              'div_vs_M0', None if not s['first_divergence_vs_M0'] else s['first_divergence_vs_M0']['index'])
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', required=True)
    p.add_argument('--prepare', action='store_true')
    p.add_argument('--analyze', action='store_true')
    p.add_argument('--variant', choices=VARIANTS)
    p.add_argument('--inputs-sha256')
    args = p.parse_args(argv)
    directory = (ROOT/args.directory).resolve()
    for protected in PROTECTED:
        output_path(directory/'__probe__', protected)
    if sum([args.prepare, args.analyze, bool(args.variant)]) != 1:
        raise ValueError('Exactly one of --prepare, --analyze, --variant')
    if args.prepare:
        if args.inputs_sha256:
            raise ValueError('Prepare must be separate from execution')
        return prepare(directory)
    if args.analyze:
        return analyze(directory, args.inputs_sha256)
    return run_variant(directory, args.variant, args.inputs_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
