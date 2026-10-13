"""Повторный аудит: новые evidence, неизменяемые runs; Q1/Q2 без stand.evaluate/compare."""
import hashlib
import importlib
import importlib.util
import io
import itertools
import json
import math
import sys
import tarfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'pyproject.toml').is_file())
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'src')]
INPUT = ROOT / 'results/evidence/W2_P2-1_remote_55404980'
OUT = Path(__file__).resolve().parent
RUN = ROOT / 'runs/W2-P2-1-55404980'
YEAR = 31557600.0
YEARS = (1.0, 10.0, 100.0, 1000.0, 10000.0)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def key(row):
    return row['orbit_id'], row['years'], row['variant']


def ensure(condition, message):
    if not condition:
        raise ValueError(message)


def finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (tuple, list)):
        return all(finite(v) for v in value)
    return True


def validate_rows(data, cases):
    expected = {(c, y, v) for c in cases for y in YEARS
                for v in ('D', 'T', 'B') if v != 'B' or y <= 1000}
    ensure(len(data) == len(expected) == 672, 'count != 672')
    ensure(len({key(r) for r in data}) == 672, 'duplicate jobs')
    ensure({key(r) for r in data} == expected, 'wrong grid')
    ensure(all(finite(r) for r in data), 'nonfinite value')
    for row in data:
        ensure(row['run_status'] == 'completed' and row['error'] is None
               and row['stopped'] is False, 'job failed/partial/error')
        end = row['last_valid_time']
        ensure(0 <= end <= row['years'] * YEAR, 'time outside horizon')
        ensure(len(row['final_probe']) == 6, 'final state shape')
        samples = row['samples']
        ensure(bool(samples) and samples[0][0] == 0, 'missing initial sample')
        times = [t for t, _ in samples]
        ensure(all(len(s) == 6 for _, s in samples), 'sample state shape')
        ensure(all(a < b for a, b in zip(times, times[1:])), 'nonmonotonic samples')
        ensure(all(0 <= t <= end for t in times), 'sample after end')
        event = row['event']
        if event:
            ensure(event['time'] == end, 'event/end mismatch')
            expected_outcome = {'reference_contact': 'host_contact',
                                'other_contact': 'other_contact',
                                'operational_escape': 'operational_escape'}
            ensure(expected_outcome.get(event['event']) == row['outcome'], 'event/outcome mismatch')
        else:
            ensure(end == row['years'] * YEAR, 'completed without event before horizon')
            ensure(row['outcome'] in ('survived', 'unresolved'), 'unexpected non-event outcome')


def compare_independently(x, y, case):
    left, right = dict(x['samples']), dict(y['samples'])
    common = sorted(left.keys() & right.keys())
    a, period = case['a_km'], case['period_seconds']
    speed = 2 * math.pi * a / period
    tau = None
    for t in common:
        dx = left[t]
        dy = right[t]
        dr = math.sqrt(sum((dx[j] - dy[j]) ** 2 for j in range(3))) / a
        dv = math.sqrt(sum((dx[j] - dy[j]) ** 2 for j in range(3, 6))) / speed
        if max(dr, dv) > 1e-6:
            tau = t
            break
    ex, ey = x['event'], y['event']
    events = [e['time'] for e in (ex, ey) if e is not None]
    first = min(events) if events else None
    same = (x['outcome'] == y['outcome'] and bool(ex) == bool(ey)
            and (ex is None or (ex['event'], ex['body']) == (ey['event'], ey['body'])))
    checked = first is not None and (tau is None or first < tau)
    dt = abs(ex['time'] - ey['time']) / period if ex and ey else None
    q2 = (same and dt is not None and dt <= 1e-6) if checked else (
        same if first is None and tau is None else True)
    return dict(tau_seconds=tau, common_samples=len(common),
                event_checked_before_tau=checked, same_outcome=same, Q2=q2,
                dt_periods=dt, event_time=first)


def main():
    manifest = load(INPUT / 'evidence_manifest.json')['files_sha256']
    ensure(all(sha(INPUT / p) == h for p, h in manifest.items()), 'old evidence manifest mismatch')
    transfer = load(INPUT / 'transfer_verification.json')
    archive = ROOT / 'scratch/p2_rental/results_55404980.tar.gz'
    ensure(sha(archive) == transfer['archive_sha256'], 'archive SHA mismatch')
    for item in transfer['files']:
        p = ROOT / item['path']
        ensure(p.stat().st_size == item['bytes'] and sha(p) == item['sha256'], 'file SHA mismatch')
    with tarfile.open(archive, 'r:gz') as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        ensure(len(members) == 19 and len({m.name for m in members}) == 19, 'archive members')
        for m in members:
            p = ROOT / m.name if m.name.startswith('runs/') else INPUT / Path(m.name).name
            stream = tar.extractfile(m)
            ensure(stream is not None, 'archive stream')
            ensure(hashlib.sha256(stream.read()).hexdigest() == sha(p), 'archive extraction mismatch')
    bundle_manifest = load(INPUT / 'bundle_manifest.json')['files']
    bundle = ROOT / 'scratch/p2_rental/bundle.tar.gz'
    ensure(sha(bundle) == load(INPUT / 'bundle_info.json')['sha256'], 'bundle SHA mismatch')
    with tarfile.open(bundle, 'r:gz') as tar:
        for p, h in bundle_manifest.items():
            stream = tar.extractfile(p)
            ensure(stream is not None, 'bundle stream')
            ensure(hashlib.sha256(stream.read()).hexdigest() == h, 'bundle content mismatch')
        for p in ('scripts/admission_p2_horizons.py', 'src/submoon_research/workflows/w2_comparison.py'):
            ensure(sha(ROOT / p) == bundle_manifest[p], 'evaluator/input code differs')
    versions = dict(line.split('==', 1) for line in (INPUT / 'p2_pip_freeze.txt').read_text().splitlines()
                    if '==' in line)
    h = hashlib.sha256()
    for name in ('dynamics/engine_compare.py', 'dynamics/native_ias15.py', 'dynamics/dense_segments.py',
                 'dynamics/fast_forces.py', 'events/dense_contact.py', 'events/escape.py'):
        rel = 'src/submoon_research/' + name
        ensure(sha(ROOT / rel) == bundle_manifest[rel], 'B source differs')
        h.update(name.encode())
        h.update((ROOT / rel).read_bytes())
    for package in ('rebound', 'reboundx', 'numpy', 'scipy', 'numba'):
        h.update(f'{package}={versions[package]}'.encode())
    summary = load(RUN / 'summary.json')
    ensure(h.hexdigest() == summary['implementations']['B'], 'B implementation fingerprint')
    expected_levels = {('core', y) for y in YEARS[:-1]} | {
        ('extension', y) for y in YEARS[2:]}
    actual_levels = summary['levels'] + summary['extension_levels']
    ensure(len(actual_levels) == 7 and
           {(s['kind'], s['years']) for s in actual_levels} == expected_levels, 'wrong level set')
    ensure(summary['years'] == list(YEARS) and summary['full_scope'] is True
           and summary['verdict_passed'] is True and summary['extension_passed'] is True,
           'summary scope/verdict')
    ensure(all(s['passed'] and s['complete'] and s['counted'] and not s['stopped']
               and not s['Q1_failures'] and not s['Q2_violations'] and not s['problems']
               for s in actual_levels), 'summary level status')
    data = rows(RUN / 'jobs.jsonl')
    workflow = importlib.import_module('submoon_research.workflows.w2_comparison')
    _, case_list = workflow.load_cases(ROOT)
    cases = {c['orbit_id']: c for c in case_list}
    ensure(len(cases) == 48 and set(cases) == set(summary['cases']), 'cases')
    validate_rows(data, cases)
    seed_info = load(INPUT / 'seed_info.json')
    old_path = ROOT / seed_info['source_jobs']
    ensure(sha(old_path) == seed_info['source_sha256'], 'old jobs SHA')
    old = {key(r): r for r in rows(old_path)}
    seed = [r for r in data if r['years'] == 10000]
    ensure(len(seed) == 96 and all(r == old[key(r)] for r in seed), 'seed differs from source')
    repeat = [r for r in data if r['years'] <= 1000 and r['variant'] in ('D', 'T')]
    ensure(len(repeat) == 384, 'repeat count')
    ensure(all({k: v for k, v in r.items() if k != 'wall'} ==
               {k: v for k, v in old[key(r)].items() if k != 'wall'} for r in repeat), 'D/T not exact')
    by = {key(r): r for r in data}
    pair_rows = []
    for years in YEARS:
        stored = load(RUN / f'level_{years:g}y.json')
        stored_pairs = { (p['orbit_id'], p['pair']): p
                         for part in ('core', 'extension') if stored[part]
                         for p in stored[part]['pairs'] }
        for orbit, case in cases.items():
            variants = ('D', 'T', 'B') if years <= 1000 else ('D', 'T')
            for a, b in itertools.combinations(variants, 2):
                result = compare_independently(by[orbit, years, a], by[orbit, years, b], case)
                reference = stored_pairs[orbit, f'{a}-{b}']
                for field in ('tau_seconds', 'event_checked_before_tau', 'same_outcome', 'Q2', 'dt_periods'):
                    ensure(result[field] == reference[field], f'pair disagreement: {orbit}/{years}/{field}')
                ensure(result['Q2'], 'independent Q2 failed')
                pair_rows.append(dict(orbit_id=orbit, years=years, pair=f'{a}-{b}', **result))
    coverage = []
    for years in YEARS:
        pp = [p for p in pair_rows if p['years'] == years]
        coverage.append(dict(years=years, pairs=len(pp), Q2_failed=sum(not p['Q2'] for p in pp),
                             only_initial=sum(p['common_samples'] == 1 for p in pp),
                             events_checked=sum(p['event_checked_before_tau'] for p in pp),
                             after_tau=sum(p['event_time'] is not None and not p['event_checked_before_tau'] for p in pp)))
    unresolved = [dict(orbit_id=r['orbit_id'], years=r['years'], variant=r['variant'])
                  for r in data if r['outcome'] == 'unresolved']
    negative_controls = []
    for name, mutation in (
        ('missing_last_level', lambda d: [r for r in d if r['years'] != 10000]),
        ('duplicate', lambda d: d[:-1] + [d[0]]),
        ('nan', lambda d: [dict(d[0], wall=float('nan'))] + d[1:]),
    ):
        try:
            validate_rows(mutation(data), cases)
        except ValueError as exc:
            negative_controls.append(dict(name=name, rejected=True, reason=str(exc)))
        else:
            raise ValueError('review negative control accepted: ' + name)
    # Испытываем именно прежний скрипт на копии без правки его файла/оригинальных runs.
    spec = importlib.util.spec_from_file_location('previous_audit', INPUT / 'audit_results.py')
    ensure(spec is not None and spec.loader is not None, 'audit module')
    previous = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(previous)
    negative_root = ROOT / 'scratch/pi_p2review_20261012/negative'
    negative_root.mkdir(parents=True, exist_ok=True)
    s = json.loads(json.dumps(summary))
    s['years'] = [y for y in s['years'] if y != 10000]
    s['extension_levels'] = [x for x in s['extension_levels'] if x['years'] != 10000]
    (negative_root / 'summary.json').write_text(json.dumps(s), encoding='utf-8')
    (negative_root / 'jobs.jsonl').write_text('\n'.join(json.dumps(r) for r in data if r['years'] != 10000) + '\n', encoding='utf-8')
    previous.RUN = negative_root
    previous.OUT = negative_root / 'old_audit_output'
    with redirect_stdout(io.StringIO()):
        previous.main()
    false_pass = load(previous.OUT / 'data_audit.json')['passed']
    ensure(false_pass, 'expected demonstration changed')
    output = dict(utc=datetime.now(timezone.utc).isoformat(), run_id='W2-P2-1-55404980',
                  evidence_hashes_verified=len(manifest), transfer_files_verified=19,
                  bundle_files_verified=len(bundle_manifest), B_fingerprint_reconstructed=h.hexdigest(),
                  jobs=672, seed_exact=96, repeat_DT_exact=384, Q2_pairs=len(pair_rows),
                  Q2_independently_reproduced=True, coverage=coverage, unresolved=unresolved,
                  negative_controls=negative_controls,
                  previous_audit_false_pass_missing_10000_level=false_pass,
                  limitations=['Q2 воспроизведён независимым кодом, не независимыми интеграциями/силами.',
                               'Проверка tau по редкой сетке не доказывает непрерывную точность.',
                               'Криптографическая целостность не удостоверяет корректность физики.'])
    (OUT / 'review.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))
    (OUT / 'pairs.json').write_text(json.dumps(pair_rows, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
