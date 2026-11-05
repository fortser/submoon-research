"""W2-P014 step 1: prepare G/GF inputs, run each once, analyze against P00/P10 of W2-P011.

Each variant is a separate process (see run_ias15_cm_frame.py). No reruns: an exclusive
<variant>.started marker is created before integration.
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
    atomic_json, file_digest, output_path, read_json,
)
from submoon_research.diagnostics.ias15_cm_frame import (  # noqa: E402
    PREDICTIONS, VARIANTS, classify, cm_transform, min_unclipped_dt, physical_prs23_dt, run_cm,
)
from submoon_research.diagnostics.ias15_frame_controls import compare_samples  # noqa: E402
from submoon_research.diagnostics.ias15_replay import environment, load_snapshot  # noqa: E402

RUN_ID = 'W2-B-cmframe-20261020'
FRAMES = ROOT/'results/evidence/W2_B_frames_20261019'
MEMORY = ROOT/'results/evidence/W2_B_memory_20261020'
PINNED = {FRAMES/'matrix/inputs.json': '243acb30b4e5882911f10da99553ad10ff9052c421e720d70c0b266673b5a083',
          FRAMES/'matrix/P00.json': 'fa5d61d86d989b45b45a59806c1366f3393d65e0b6199be5f43ac0fc25e7823c',
          FRAMES/'matrix/P10.json': 'b5ee93c251bc0b813039d81fdaf3d6bff3eb6dd95b62fa2c15a6e38b24babfcd',
          SNAPSHOT.parent/'physical.json': 'd79aa9625ca22fa326774f0875857312f57e277002626452ebc4f48cfbd05408',
          SNAPSHOT: SNAPSHOT_SHA, REFERENCE: REFERENCE_SHA}
PROTECTED = (SNAPSHOT.parent, REFERENCE.parent, FRAMES, MEMORY, ROOT/'runs/W2-P2-2a-56220810')


def code_hashes():
    files = [Path(__file__), ROOT/'scripts/run_ias15_cm_frame.py', ROOT/'scripts/replay_ias15_snapshot.py',
             ROOT/'scripts/diag_ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_cm_frame.py',
             ROOT/'src/submoon_research/diagnostics/ias15_memory_controls.py',
             ROOT/'src/submoon_research/diagnostics/ias15_frame_controls.py',
             ROOT/'src/submoon_research/diagnostics/ias15_controller.py',
             ROOT/'src/submoon_research/diagnostics/ias15_replay.py',
             ROOT/'src/submoon_research/contracts/p2_files.py']
    files += list((ROOT/'src/submoon_research/dynamics').glob('*.py'))
    return {p.relative_to(ROOT).as_posix(): file_digest(p) for p in sorted(files)}


def _pinned():
    for path, sha in PINNED.items():
        if file_digest(path) != sha:
            raise ValueError('Pinned source changed: '+str(path.relative_to(ROOT)))


def _new(path):
    for protected in PROTECTED:
        output_path(path, protected)
    return path


def prepare(directory):
    _pinned()
    reference = read_json(REFERENCE)
    if reference['identity']['B_implementation'] != replay.b_fingerprint():
        raise ValueError('B implementation changed')
    _, manifest = load_snapshot(SNAPSHOT, SNAPSHOT_SHA, expected_identity=reference['identity'])
    physical = read_json(SNAPSHOT.parent/'physical.json')
    frames = read_json(FRAMES/'matrix/inputs.json')
    scales = read_json(FRAMES/'comparison.json')
    if frames['grid']['original_t0'] != physical['time']:
        raise ValueError('Grid origin differs from snapshot time')
    configs = {v: cm_transform(physical, v) for v in VARIANTS}
    # All geometry checks above precede creation of executable inputs.
    directory.mkdir(parents=True, exist_ok=False)
    inputs = dict(run_id=RUN_ID, task='W2-T042', plan='W2-P014', snapshot_sha256=SNAPSHOT_SHA,
                  pinned_sha256={str(p.relative_to(ROOT).as_posix()): s for p, s in PINNED.items()},
                  identity=reference['identity'], environment=manifest['environment'], grid=frames['grid'],
                  variants=configs, predictions=PREDICTIONS,
                  radius_scale_km=scales['radius_scale_km'], speed_scale_km_s=scales['speed_scale_km_s'],
                  code_sha256=code_hashes(), max_steps=10000, wall_seconds=60., scientific_admission=False)
    atomic_json(directory/'inputs.json', inputs)
    print(file_digest(directory/'inputs.json'))
    return 0


def _check(directory, inputs_sha):
    if not inputs_sha or file_digest(directory/'inputs.json') != inputs_sha:
        raise ValueError('Execution requires pinned inputs SHA')
    inputs = read_json(directory/'inputs.json')
    _pinned()
    if (inputs['code_sha256'] != code_hashes() or inputs['environment'] != environment()
            or inputs['identity']['B_implementation'] != replay.b_fingerprint()):
        raise ValueError('Inputs/environment/code identity changed')
    return inputs


def run_variant(directory, variant, inputs_sha):
    inputs = _check(directory, inputs_sha)
    output = _new(directory/(variant+'.json'))
    with (directory/(variant+'.started')).open('x', encoding='utf-8') as handle:
        handle.write(inputs_sha+'\n')
    result = run_cm(inputs['variants'][variant], inputs['identity'], inputs['grid'],
                    wall_seconds=inputs['wall_seconds'], max_steps=inputs['max_steps'])
    result.update(run_id=RUN_ID+'-'+variant, inputs_sha256=inputs_sha, code_sha256=inputs['code_sha256'],
                  environment=environment())
    atomic_json(output, result)
    print({k: result[k] for k in ('variant', 'status', 'attempts', 'accepted_steps',
                                  'last_valid_elapsed', 'wall_seconds')}, 'min dt', min_unclipped_dt(result))
    return 0


def analyze(directory, inputs_sha):
    inputs = _check(directory, inputs_sha)
    results = {v: read_json(directory/(v+'.json')) for v in VARIANTS if (directory/(v+'.json')).exists()}
    others = {'P00': read_json(FRAMES/'matrix/P00.json'), 'P10': read_json(FRAMES/'matrix/P10.json')}
    scale = dict(radius_scale=inputs['radius_scale_km'], speed_scale=inputs['speed_scale_km_s'])
    gms, eps = inputs['variants']['G']['gms'], inputs['variants']['G']['adaptation']['epsilon']
    summary = {}
    for v, r in results.items():
        phys = [physical_prs23_dt(rec['absolute_state'], gms, eps) for rec in r['records']]
        i = min(range(len(r['records'])), key=lambda k: r['records'][k]['dt_done'])
        summary[v] = dict(status=r['status'], target_reached=r['target_reached'], attempts=r['attempts'],
                          accepted_steps=r['accepted_steps'], last_valid_elapsed=r['last_valid_elapsed'],
                          failure=r['failure'], min_unclipped_dt_s=min_unclipped_dt(r),
                          dt_quantiles_s=r['dt_quantiles_s'], physical_prs23_dt_min_s=min(phys),
                          physical_prs23_dt_at_min_actual_s=phys[i],
                          max_abs_coordinate_km=inputs['variants'][v]['max_abs_coordinate_km'],
                          coordinate_ulp_km=inputs['variants'][v]['coordinate_ulp_km'],
                          vs={k: compare_samples(r, o, **scale) for k, o in others.items()},
                          wall_seconds=r['wall_seconds'])
        if 'GF' in results and v == 'G':
            summary[v]['vs']['GF'] = compare_samples(r, results['GF'], **scale)
    reference = {k: dict(status=o['status'], min_unclipped_dt_s=min_unclipped_dt(o)) for k, o in others.items()}
    verdict = classify(results['G'], results['GF']) if len(results) == 2 else 'incomplete'
    flags = [f'{v} vs {k}: {c["max_position_difference_km"]*1e3:.3g} m > 10 m'
             for v, s in summary.items() for k, c in s['vs'].items()
             if c['max_position_difference_km'] is not None and c['max_position_difference_km'] > 0.01]
    out = dict(run_id=RUN_ID, inputs_sha256=inputs_sha, predictions=inputs['predictions'], verdict=verdict,
               trajectory_flags=flags, variants=summary, reference_P011=reference,
               source_hashes={v+'.json': file_digest(directory/(v+'.json')) for v in results},
               scientific_admission=False,
               interpretation='single snapshot window; not a production fix or admission of B')
    atomic_json(_new(directory/'comparison.json'), out)
    print('VERDICT', verdict, flags)
    for v, s in summary.items():
        print(v, s['status'], 'attempts', s['attempts'], 'min dt', s['min_unclipped_dt_s'],
              'physical est', round(s['physical_prs23_dt_min_s'], 3),
              {k: round(c['max_position_difference_km']*1e3, 4) for k, c in s['vs'].items()})
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
