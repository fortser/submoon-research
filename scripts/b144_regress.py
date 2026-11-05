"""W2-P014 step 4 (W2-T050): the 144 archived P2-2a B units at 100 and 1000 years with the current B.

Modes
  reference  (local, once)   archived identities of the 288 B jobs -> data/manifests/W2_B144_reference_v1.json
  prepare    (server)        legacy regeneration of the starts (OPENBLAS_CORETYPE=SkylakeX on an AVX-512 CPU),
                             exact check of all 288 archived job fingerprints and initial probes; on any mismatch
                             nothing is integrated (exit 3). Writes units.json and the pre-run manifest.json.
  benchmark  (server)        3 short archived jobs -> speed ratio -> prediction of the longest job and total;
                             NO-GO (exit 4) if it does not fit the deadline with the margin.
  run        (server)        all 288 jobs once, longest archived first, 1 CPU per worker, absolute deadline and
                             stop file (stopped jobs are 'partial'); full stand rows + cheap B diagnostics.
  analyze    (local)         pre-registered rule below, comparisons with archived B/D/T, diagnostics, cost.
  devcheck   (local)         end-to-end pipeline check on a few archived jobs that match locally (scratch only;
                             not part of the scientific run).

PRE-REGISTERED RULE (fixed 2026-10-21 before any rental; W2-T050)
  Primary, G-B development regression: PASSED iff all 288 planned jobs are present exactly once with the
  expected job fingerprints, every run_status is 'completed', and every job without a terminal event ends at
  its horizon (last_valid_time == H). Any 'failed' -> NOT_PASSED (G-B stays closed; failure_context analysed).
  Any 'partial' (deadline/stop) or missing job -> INCOMPLETE, no verdict on those jobs.
  Secondary, descriptive only: one-sided 95% Clopper-Pearson upper bound of the failure frequency (a
  development regression, not an admission of a failure rate); outcome agreement with archived B (where it
  completed), D and T at the same horizon, with D/T high-e unresolved shown separately; |dt_event|/P0 for the
  same event; diagnostics (max endpoint residual, flag >= 1e-10; min dt/ULP(t), flag < 1e4; small-step events
  dt < 0.01*max_step away from start/horizon with the probe acceleration-cancellation ratio; max |x| on a
  grid of the first step and every 1000th step); cost (worker-hours, longest job, per-job wall ratio to the archive).
Not a scientific admission of B, D/T, W1 or W3.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as metadata
import json
import math
import multiprocessing as mp
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402

import admission_p2_2 as stand  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, canonical, file_digest, loads, read_json, units_digest,
)
from submoon_research.dynamics import engine_compare  # noqa: E402
from submoon_research.dynamics.native_ias15 import NativeIAS15  # noqa: E402

ARCHIVE = ROOT/'runs/W2-P2-2a-56220810'
ARCHIVE_SUMMARY_SHA = '7308d42f368bb94753904c3f1085b0afb2039c9d9c86ad4ae26776e1518f135f'
ARCHIVE_JOBS_SHA = '0c17c36ac24b7bf0f8cd6ab939ef8464e3a51daf82265ab1f637b6a5fef41175'
REFERENCE = ROOT/'data/manifests/W2_B144_reference_v1.json'
YEARS = (100.0, 1000.0)
B_UNITS = 16
BENCHMARK = (('himalia:uniform_a_e_cos_i_phases:0015', 100.0), ('himalia:canonical_volume:0012', 100.0),
             ('himalia:uniform_log_a:0014', 100.0))
DEVCHECK = (('ganymede:uniform_log_a:0002', 100.0), ('himalia:canonical_volume:0010', 100.0))
SMALL_STEP_FRACTION = 0.01
RAMP_STEPS = 50
COORDINATE_EVERY = 1000
TOP_EVENTS = 20
CODE = ('scripts/b144_regress.py', 'scripts/admission_p2_2.py', 'scripts/admission_p2_horizons.py',
        'src/submoon_research/dynamics/engine_compare.py', 'src/submoon_research/dynamics/native_ias15.py',
        'src/submoon_research/dynamics/dense_segments.py', 'src/submoon_research/dynamics/fast_forces.py',
        'src/submoon_research/events/dense_contact.py', 'src/submoon_research/events/escape.py',
        'src/submoon_research/exact_linalg.py', 'requirements-w2.txt')


def utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def code_hashes():
    return {name: file_digest(ROOT/name) for name in CODE}


def environment():
    text = ''
    with contextlib.suppress(OSError):
        text = Path('/proc/cpuinfo').read_text()
    lines = text.splitlines()
    flags = next((line for line in lines if line.startswith('flags')), '')
    model = next((line.split(':', 1)[1].strip() for line in lines if line.startswith('model name')), '')
    packages = {}
    for name in ('numpy', 'scipy', 'numba', 'rebound', 'reboundx'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    return dict(python=platform.python_version(), platform=platform.platform(), cpu_model=model,
                avx512f=' avx512f' in flags, packages=packages,
                openblas_coretype=os.environ.get('OPENBLAS_CORETYPE'),
                threads={k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                                                        'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS')})


# ---------------------------------------------------------------- reference and inputs

def archive_rows():
    if (file_digest(ARCHIVE/'summary.json') != ARCHIVE_SUMMARY_SHA
            or file_digest(ARCHIVE/'jobs.jsonl') != ARCHIVE_JOBS_SHA):
        raise ValueError('Pinned P2-2a archive changed')
    rows = {}
    for line in (ARCHIVE/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        r = loads(line)
        rows[(r['orbit_id'], r['years'], r['variant'])] = r
    return read_json(ARCHIVE/'summary.json'), rows


def build_reference(path=REFERENCE):
    if path.exists():
        raise SystemExit('reference already exists; not overwritten')
    summary, rows = archive_rows()
    jobs = sorted(((o, y) for (o, y, v) in rows if v == 'B'), key=lambda k: (k[1], k[0]))
    if len(jobs) != len(YEARS)*9*B_UNITS:
        raise ValueError('Unexpected number of archived B jobs')
    entries = []
    for o, y in jobs:
        r = rows[(o, y, 'B')]
        entries.append(dict(orbit_id=o, years=y, unit=r['unit'], job_fingerprint=r['job_fingerprint'],
                            initial_probe=r['samples'][0][1], archived_wall=r['wall'], archived_steps=r['steps'],
                            archived_status=r['run_status'], archived_outcome=r['outcome']))
    doc = dict(schema='w2-b144-reference-v1', task='W2-T050', source_run=ARCHIVE.relative_to(ROOT).as_posix(),
               source_summary_sha256=ARCHIVE_SUMMARY_SHA, source_jobs_sha256=ARCHIVE_JOBS_SHA,
               implementations=summary['implementations'], generator_numerics=summary.get('generator_numerics'),
               archived_machine=summary['machine']['lscpu'].get('Model name'),
               archived_b_worker_hours=sum(e['archived_wall'] for e in entries)/3600,
               archived_longest_hours=max(e['archived_wall'] for e in entries)/3600, jobs=entries)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, doc)
    print(file_digest(path), len(entries), 'jobs')
    return 0


def load_reference(sha256):
    if not sha256 or file_digest(REFERENCE) != sha256:
        raise ValueError('Reference SHA does not match the pinned value')
    doc = read_json(REFERENCE)
    if doc.get('schema') != 'w2-b144-reference-v1' or len(doc['jobs']) != len(YEARS)*9*B_UNITS:
        raise ValueError('Unexpected reference document')
    return doc


def regenerate_units(reference):
    from submoon_research.exact_linalg import LEGACY, numerics
    with numerics(reference.get('generator_numerics') or LEGACY):
        prepared = stand.setups()
        return stand.units(B_UNITS, 0, prepared)


def check_identity(units, reference, job_fingerprint=None):
    """Exact archived identity of every planned job: full job fingerprint and initial probe state."""
    job_fingerprint = job_fingerprint or stand.job_fingerprint
    by_id = {u['orbit_id']: u for u in units}
    mismatches = []
    for e in reference['jobs']:
        unit = by_id.get(e['orbit_id'])
        if unit is None:
            mismatches.append(dict(orbit_id=e['orbit_id'], years=e['years'], reason='missing unit'))
            continue
        fp_ok = job_fingerprint(unit, e['years'], 'B', reference['implementations']) == e['job_fingerprint']
        probe_ok = canonical(list(unit['state'][-6:])) == canonical(list(e['initial_probe']))
        if not (fp_ok and probe_ok):
            mismatches.append(dict(orbit_id=e['orbit_id'], years=e['years'], job_fingerprint=fp_ok,
                                   initial_probe=probe_ok))
    return mismatches


def plan_order(reference):
    """Longest archived job first (the tail determines the wall time); ties by key."""
    walls = {(e['orbit_id'], e['years']): e['archived_wall'] for e in reference['jobs']}
    return sorted(walls, key=lambda k: (-walls[k], k))


def _write_inputs(run_dir, units, reference, reference_sha, new_b, plan, scientific):
    atomic_json(run_dir/'units.json', dict(schema='w2-b144-units-v1', units_sha256=units_digest(units), units=units))
    manifest = dict(schema='w2-b144-manifest-v1', task='W2-T050', created_utc=utc(), rule=__doc__,
                    scientific=scientific, reference_sha256=reference_sha,
                    units_file_sha256=file_digest(run_dir/'units.json'), units_sha256=units_digest(units),
                    b_implementation=new_b, archived_implementations=reference['implementations'],
                    plan=[dict(orbit_id=o, years=y) for o, y in plan], code_sha256=code_hashes(),
                    environment=environment(),
                    limits=dict(cpu_per_worker=1, small_step_fraction=SMALL_STEP_FRACTION, ramp_steps=RAMP_STEPS,
                                coordinate_every=COORDINATE_EVERY))
    atomic_json(run_dir/'manifest.json', manifest)
    print('manifest', file_digest(run_dir/'manifest.json'), 'jobs', len(plan), flush=True)


def prepare(run_dir, expected_b, reference_sha):
    reference = load_reference(reference_sha)
    new_b = engine_compare.b_implementation_fingerprint()
    if new_b != expected_b or stand.b_fingerprint() != expected_b:
        print('B implementation differs from the tested version:', new_b, flush=True)
        return 5
    run_dir.mkdir(parents=True, exist_ok=False)
    units = regenerate_units(reference)
    mismatches = check_identity(units, reference)
    atomic_json(run_dir/'identity.json', dict(passed=not mismatches, checked=len(reference['jobs']),
                                              mismatch_count=len(mismatches), utc=utc(), environment=environment(),
                                              mismatches=mismatches))
    if mismatches:
        print(f'IDENTITY MISMATCH: {len(mismatches)} of {len(reference["jobs"])} jobs; nothing integrated', flush=True)
        return 3
    wanted = {e['orbit_id'] for e in reference['jobs']}
    _write_inputs(run_dir, [u for u in units if u['orbit_id'] in wanted], reference, reference_sha, new_b,
                  plan_order(reference), scientific=True)
    return 0


def load_manifest(run_dir, sha):
    if not sha or file_digest(run_dir/'manifest.json') != sha:
        raise ValueError('Manifest SHA does not match')
    manifest = read_json(run_dir/'manifest.json')
    if file_digest(run_dir/'units.json') != manifest['units_file_sha256']:
        raise ValueError('units.json changed after the manifest')
    return manifest


def build_jobs(run_dir, manifest, deadline=None, stop_file=None):
    units = {u['orbit_id']: u for u in read_json(run_dir/'units.json')['units']}
    implementations = dict(manifest['archived_implementations'], B=manifest['b_implementation'])
    return [dict(unit=units[p['orbit_id']], years=p['years'], variant='B', deadline=deadline, stop_file=stop_file,
                 fingerprint=stand.job_fingerprint(units[p['orbit_id']], p['years'], 'B', implementations))
            for p in manifest['plan']]


# ---------------------------------------------------------------- diagnostics inside B

def probe_cancellation(sim, count):
    """|sum a_i| / sum |a_i| for the probe from point masses (G=1, m=GM); 1 = no cancellation."""
    p = sim.particles[count-1]
    total, magnitude = np.zeros(3), 0.
    for i in range(count-1):
        b = sim.particles[i]
        d = np.array([b.x-p.x, b.y-p.y, b.z-p.z])
        a = b.m*d/float(np.linalg.norm(d))**3
        total += a
        magnitude += float(np.linalg.norm(a))
    return float(np.linalg.norm(total))/magnitude if magnitude else None


class Monitor(NativeIAS15):
    """Bookkeeping around NativeIAS15.step; the integration itself is untouched."""
    stats: dict = {}

    @classmethod
    def reset(cls):
        cls.stats = dict(steps=0, min_dt=None, min_dt_time=None, min_dt_over_ulp_t=None,
                         min_dt_over_max_step=None, min_dt_over_max_step_at=None, steps_at_max_step=0,
                         max_endpoint_residual=0., small_step_events=0, min_cancellation_ratio=None,
                         events=[], max_abs_coordinate_km=0., coordinate_every=COORDINATE_EVERY,
                         coordinate_samples=0, failure_context=None, diagnostic_error=None)

    def step(self, bound, max_step=np.inf):
        s = Monitor.stats
        left, proposed = float(self.sim.t), float(self.sim.dt)
        try:
            segment = super().step(bound, max_step)
        except Exception:
            try:
                s['failure_context'] = dict(left=left, right=float(self.sim.t), proposed_dt=proposed,
                                            dt_last_done=float(self.sim.dt_last_done), next_dt=float(self.sim.dt),
                                            max_step=float(max_step), bound=float(bound), ulp_t=math.ulp(left),
                                            endpoint_residual=getattr(self, 'checked_endpoint_residual', None),
                                            cancellation_ratio=probe_cancellation(self.sim, self.count))
            except Exception as exc:  # noqa: BLE001 — diagnostics must never replace the numerical error
                s['diagnostic_error'] = repr(exc)
            raise
        try:
            self._account(s, bound, max_step, left)
        except Exception as exc:  # noqa: BLE001 — a diagnostics bug must not become a B failure
            s['diagnostic_error'] = repr(exc)
        return segment

    def _account(self, s, bound, max_step, left):
        dt, t = float(self.sim.dt_last_done), float(self.sim.t)
        s['steps'] += 1
        if s['min_dt'] is None or dt < s['min_dt']:
            s['min_dt'], s['min_dt_time'] = dt, t
        ratio = dt/math.ulp(t)
        if s['min_dt_over_ulp_t'] is None or ratio < s['min_dt_over_ulp_t']:
            s['min_dt_over_ulp_t'] = ratio
        s['max_endpoint_residual'] = max(s['max_endpoint_residual'], float(self.last_endpoint_residual))
        if math.isfinite(max_step) and max_step > 0:
            frac = dt/max_step
            if dt >= 0.999*max_step:
                s['steps_at_max_step'] += 1
            interior = s['steps'] > RAMP_STEPS and bound-left > max_step
            if interior and (s['min_dt_over_max_step'] is None or frac < s['min_dt_over_max_step']):
                s['min_dt_over_max_step'] = frac
                s['min_dt_over_max_step_at'] = dict(t=t, dt=dt, max_step=float(max_step))
            if interior and frac < SMALL_STEP_FRACTION:
                ratio_a = probe_cancellation(self.sim, self.count)
                s['small_step_events'] += 1
                if ratio_a is not None and (s['min_cancellation_ratio'] is None or ratio_a < s['min_cancellation_ratio']):
                    s['min_cancellation_ratio'] = ratio_a
                s['events'].append(dict(t=t, dt=dt, max_step=float(max_step), cancellation_ratio=ratio_a))
                s['events'] = sorted(s['events'], key=lambda e: e['dt']/e['max_step'])[:TOP_EVENTS]
        if s['steps'] == 1 or s['steps'] % COORDINATE_EVERY == 0:  # first step and every 1000th
            s['coordinate_samples'] += 1
            s['max_abs_coordinate_km'] = max(s['max_abs_coordinate_km'],
                                             float(np.max(np.abs(self.absolute_state()[:, :3]))))


def _worker_init():
    stand._init()
    engine_compare.NativeIAS15 = Monitor


def run_one(job):
    Monitor.reset()
    row = stand._job(job)
    row['b_diagnostics'] = dict(Monitor.stats)
    row['runner'] = dict(task='W2-T050', pid=os.getpid(), finished_utc=utc())
    return row


def pool_rows(jobs, workers):
    with contextlib.ExitStack() as stack:
        if workers > 0:
            pool = stack.enter_context(mp.get_context('spawn').Pool(workers, initializer=_worker_init))
            yield from pool.imap_unordered(run_one, jobs, chunksize=1)
        else:
            _worker_init()
            yield from map(run_one, jobs)


# ---------------------------------------------------------------- benchmark and run

def predict(ratios, reference, workers, margin):
    r = statistics.median(ratios)
    longest = max(e['archived_wall'] for e in reference['jobs'])*r
    total = sum(e['archived_wall'] for e in reference['jobs'])*r
    wall = max(longest, total/max(workers, 1))
    return dict(ratio=r, predicted_longest_s=longest, predicted_total_worker_s=total,
                predicted_wall_s=wall, with_margin_s=wall*margin, margin=margin)


def benchmark(run_dir, manifest_sha, deadline_utc, workers, margin):
    manifest = load_manifest(run_dir, manifest_sha)
    if file_digest(REFERENCE) != manifest['reference_sha256']:
        raise ValueError('Reference changed')
    reference = read_json(REFERENCE)
    wanted = set(BENCHMARK)
    jobs = [j for j in build_jobs(run_dir, manifest) if (j['unit']['orbit_id'], j['years']) in wanted]
    archived = {(e['orbit_id'], e['years']): e['archived_wall'] for e in reference['jobs']}
    started = time.monotonic()
    rows = list(pool_rows(jobs, len(jobs)))
    ratios = [r['wall']/archived[(r['orbit_id'], r['years'])] for r in rows if r['run_status'] == 'completed']
    remaining = stand.parse_deadline(deadline_utc)-time.time()
    result = dict(utc=utc(), jobs=[dict(orbit_id=r['orbit_id'], years=r['years'], status=r['run_status'],
                                        wall=r['wall'], archived_wall=archived[(r['orbit_id'], r['years'])])
                                   for r in rows],
                  wall_seconds=time.monotonic()-started, workers=workers, remaining_s=remaining,
                  deadline_utc=deadline_utc)
    if len(ratios) != len(jobs) or not jobs:
        result.update(verdict='NO-GO', reason='benchmark job not completed')
    else:
        result.update(predict(ratios, reference, workers, margin))
        result['verdict'] = 'GO' if result['with_margin_s'] <= remaining else 'NO-GO'
    atomic_json(run_dir/'benchmark.json', result)
    print(json.dumps({k: result.get(k) for k in ('verdict', 'ratio', 'predicted_longest_s', 'predicted_wall_s',
                                                  'with_margin_s', 'remaining_s')}), flush=True)
    return 0 if result['verdict'] == 'GO' else 4


def _counts(rows):
    counts: dict = {}
    for r in rows:
        counts[r['run_status']] = counts.get(r['run_status'], 0)+1
    return counts


def run(run_dir, manifest_sha, deadline_utc, stop_file, workers):
    manifest = load_manifest(run_dir, manifest_sha)
    if (manifest['code_sha256'] != code_hashes()
            or engine_compare.b_implementation_fingerprint() != manifest['b_implementation']):
        raise ValueError('Code or B implementation changed after the manifest')
    jobs_file = run_dir/'jobs.jsonl'
    if jobs_file.exists():
        raise ValueError('jobs.jsonl exists: no reruns in this run directory')
    deadline = stand.parse_deadline(deadline_utc) if deadline_utc else None
    jobs = build_jobs(run_dir, manifest, deadline, stop_file)
    started, done = time.monotonic(), []
    progress = run_dir/'progress.json'
    with jobs_file.open('a', encoding='utf-8') as stream:
        for row in pool_rows(jobs, workers):
            stream.write(json.dumps(row, ensure_ascii=False, default=float)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
            done.append(row)
            tmp = progress.with_suffix('.tmp')
            tmp.write_text(json.dumps(dict(updated_utc=utc(), done=len(done), planned=len(jobs), counts=_counts(done),
                                           elapsed_s=time.monotonic()-started,
                                           last=dict(orbit_id=row['orbit_id'], years=row['years'],
                                                     status=row['run_status'], wall=row['wall'])),
                                      ensure_ascii=False, indent=1), encoding='utf-8')
            tmp.replace(progress)
            print(f"  {len(done)}/{len(jobs)} {row['orbit_id']} {row['years']:g} {row['run_status']} "
                  f"{row['wall']:.0f} s", flush=True)
    atomic_json(run_dir/'summary.json', dict(task='W2-T050', utc=utc(), manifest_sha256=manifest_sha,
                                             jobs_file_sha256=file_digest(jobs_file), planned=len(jobs),
                                             done=len(done), counts=_counts(done), workers=workers,
                                             deadline_utc=deadline_utc, wall_seconds=time.monotonic()-started,
                                             worker_hours=sum(r['wall'] for r in done)/3600,
                                             environment=environment()))
    print('RUN DONE', _counts(done), flush=True)
    return 0


# ---------------------------------------------------------------- local analysis

def cp_upper(failures, n, alpha=0.05):
    if n <= 0:
        return None
    if failures == 0:
        return 1-alpha**(1/n)
    from scipy.stats import beta
    return float(beta.ppf(1-alpha, failures+1, n-failures))


def evaluate(rows, planned, expected_fp, horizons):
    """Primary pre-registered rule. rows: list of job rows; planned: list of (orbit_id, years)."""
    keys = [(r['orbit_id'], r['years']) for r in rows]
    duplicates = len(keys)-len(set(keys))
    by_key = {(r['orbit_id'], r['years']): r for r in rows}
    missing = [k for k in planned if k not in by_key]
    extra = [k for k in by_key if k not in set(planned)]
    wrong_fp = [k for k, r in by_key.items() if expected_fp.get(k) != r['job_fingerprint']]
    failed = [k for k, r in by_key.items() if r['run_status'] == 'failed']
    partial = [k for k, r in by_key.items() if r['run_status'] == 'partial']
    short = [k for k, r in by_key.items() if r['run_status'] == 'completed' and r['event'] is None
             and r['last_valid_time'] != horizons.get(k)]
    if failed:
        verdict = 'NOT_PASSED'
    elif partial or duplicates or missing or extra or wrong_fp or short:
        verdict = 'INCOMPLETE'
    else:
        verdict = 'PASSED'
    attempted = len(planned)-len(partial)-len(missing)
    return dict(verdict=verdict, planned=len(planned), rows=len(rows), duplicates=duplicates, missing=missing,
                extra=extra, wrong_fingerprint=wrong_fp, failed=failed, partial=partial, not_at_horizon=short,
                failure_upper_95=cp_upper(len(failed), attempted))


def _label(row):
    return (row['outcome'], row.get('unresolved_reason'))


def compare_rows(new, other):
    ev_n, ev_o = new.get('event'), other.get('event')
    dt = None
    if ev_n and ev_o and (ev_n['event'], ev_n['body']) == (ev_o['event'], ev_o['body']):
        dt = abs(ev_n['time']-ev_o['time'])
    return dict(same_label=_label(new) == _label(other), new=list(_label(new)), other=list(_label(other)),
                event_dt_s=dt)


def analyze(run_dir, manifest_sha, output):
    manifest = load_manifest(run_dir, manifest_sha)
    _, archived = archive_rows()
    units = {u['orbit_id']: u for u in read_json(run_dir/'units.json')['units']}
    rows = [loads(line) for line in (run_dir/'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    planned = [(p['orbit_id'], p['years']) for p in manifest['plan']]
    implementations = dict(manifest['archived_implementations'], B=manifest['b_implementation'])
    expected_fp = {k: stand.job_fingerprint(units[k[0]], k[1], 'B', implementations) for k in planned}
    horizons = {k: k[1]*stand.YEAR for k in planned}
    primary = evaluate(rows, planned, expected_fp, horizons)
    by_key = {(r['orbit_id'], r['years']): r for r in rows}
    comparisons, discord = {}, {}
    for name in ('B_old', 'D', 'T'):
        variant = 'B' if name == 'B_old' else name
        items = []
        for k, r in by_key.items():
            other = archived.get((k[0], k[1], variant))
            if other is None or r['run_status'] != 'completed':
                continue
            if name == 'B_old' and other['run_status'] != 'completed':
                continue
            c = compare_rows(r, other)
            c.update(orbit_id=k[0], years=k[1], period_s=units[k[0]]['period_seconds'])
            items.append(c)
        comparisons[name] = dict(
            pairs=len(items), discordant=sum(not c['same_label'] for c in items),
            discordant_other_high_e=sum((not c['same_label']) and c['other'][1] == 'high_e_prolonged' for c in items),
            max_event_dt_over_P0=max((c['event_dt_s']/c['period_s'] for c in items if c['event_dt_s'] is not None),
                                     default=None))
        discord[name] = [c for c in items if not c['same_label']]
    diags = [r.get('b_diagnostics') or {} for r in rows]

    def values(key):
        return [d[key] for d in diags if d.get(key) is not None]

    diagnostics = dict(
        max_endpoint_residual=max(values('max_endpoint_residual'), default=None),
        jobs_endpoint_residual_ge_1e10=sum(1 for v in values('max_endpoint_residual') if v >= 1e-10),
        min_dt_over_ulp_t=min(values('min_dt_over_ulp_t'), default=None),
        jobs_dt_over_ulp_lt_1e4=sum(1 for v in values('min_dt_over_ulp_t') if v < 1e4),
        jobs_with_small_step_events=sum(1 for d in diags if d.get('small_step_events')),
        small_step_events=sum(d.get('small_step_events', 0) for d in diags),
        min_cancellation_ratio=min(values('min_cancellation_ratio'), default=None),
        max_abs_coordinate_km=max((d['max_abs_coordinate_km'] for d in diags if d.get('coordinate_samples')),
                                  default=None),
        jobs_without_coordinate_sample=sum(1 for d in diags if not d.get('coordinate_samples')),
        coordinate_every_steps=COORDINATE_EVERY,
        jobs_with_diagnostic_error=sum(1 for d in diags if d.get('diagnostic_error')),
        failure_contexts={f"{r['orbit_id']}@{r['years']:g}": (r.get('b_diagnostics') or {}).get('failure_context')
                          for r in rows if r['run_status'] == 'failed'})
    walls = {(r['orbit_id'], r['years']): r['wall'] for r in rows if r['run_status'] == 'completed'}
    ratios = [walls[k]/archived[(k[0], k[1], 'B')]['wall'] for k in walls
              if archived[(k[0], k[1], 'B')]['run_status'] == 'completed' and archived[(k[0], k[1], 'B')]['wall'] > 1]
    cost = dict(worker_hours=sum(r['wall'] for r in rows)/3600,
                longest_hours=max((r['wall'] for r in rows), default=0)/3600,
                median_wall_ratio_to_archive=statistics.median(ratios) if ratios else None, ratio_jobs=len(ratios))
    out = dict(task='W2-T050', manifest_sha256=manifest_sha, jobs_file_sha256=file_digest(run_dir/'jobs.jsonl'),
               scientific=manifest.get('scientific'), rule=__doc__, primary=primary, comparisons=comparisons,
               discordant=discord, diagnostics=diagnostics, cost=cost, b_implementation=manifest['b_implementation'],
               scientific_admission=False)
    output = Path(output)
    if not output.is_absolute():
        output = ROOT/output
    if output.exists():
        raise SystemExit('analysis output exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(output, out)
    print(json.dumps(dict(verdict=primary['verdict'], failed=len(primary['failed']), partial=len(primary['partial']),
                          upper_95=primary['failure_upper_95'],
                          discordant={k: v['discordant'] for k, v in comparisons.items()},
                          diagnostics={k: v for k, v in diagnostics.items() if k != 'failure_contexts'},
                          cost=cost), ensure_ascii=False, indent=1, default=str))
    return 0


def devcheck(run_dir, expected_b, reference_sha, workers):
    """Local end-to-end check of prepare->run->analyze on a few jobs that match on this CPU (not scientific)."""
    if 'scratch' not in run_dir.parts:
        raise ValueError('devcheck writes only under scratch/')
    reference = load_reference(reference_sha)
    new_b = engine_compare.b_implementation_fingerprint()
    if new_b != expected_b:
        print('B implementation differs from the tested version:', new_b, flush=True)
        return 5
    run_dir.mkdir(parents=True, exist_ok=False)
    wanted = set(DEVCHECK)
    sub = dict(reference, jobs=[e for e in reference['jobs'] if (e['orbit_id'], e['years']) in wanted])
    units = regenerate_units(reference)
    mismatches = check_identity(units, sub)
    if mismatches or len(sub['jobs']) != len(wanted):
        print('devcheck identity mismatch', mismatches, flush=True)
        return 3
    ids = {o for o, _ in wanted}
    _write_inputs(run_dir, [u for u in units if u['orbit_id'] in ids], sub, reference_sha, new_b,
                  plan_order(sub), scientific=False)
    sha = file_digest(run_dir/'manifest.json')
    code = run(run_dir, sha, None, None, workers)
    if code:
        return code
    return analyze(run_dir, sha, run_dir/'analysis.json')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('mode', choices=('reference', 'prepare', 'benchmark', 'run', 'analyze', 'devcheck'))
    p.add_argument('--run-dir')
    p.add_argument('--expected-b')
    p.add_argument('--reference-sha256')
    p.add_argument('--manifest-sha256')
    p.add_argument('--deadline-utc')
    p.add_argument('--stop-file')
    p.add_argument('--workers', type=int, default=1)
    p.add_argument('--margin', type=float, default=1.25)
    p.add_argument('--output')
    args = p.parse_args(argv)
    if args.mode == 'reference':
        return build_reference()
    run_dir = (ROOT/args.run_dir).resolve()
    if args.mode == 'prepare':
        return prepare(run_dir, args.expected_b, args.reference_sha256)
    if args.mode == 'benchmark':
        return benchmark(run_dir, args.manifest_sha256, args.deadline_utc, args.workers, args.margin)
    if args.mode == 'run':
        return run(run_dir, args.manifest_sha256, args.deadline_utc, args.stop_file, args.workers)
    if args.mode == 'devcheck':
        return devcheck(run_dir, args.expected_b, args.reference_sha256, args.workers)
    return analyze(run_dir, args.manifest_sha256, args.output)


if __name__ == '__main__':
    raise SystemExit(main())
