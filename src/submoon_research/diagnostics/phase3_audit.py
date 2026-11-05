"""W2-T064: независимый read-only аудит файлов Фазы 3, без интеграций.

Не заменяет замороженный классификатор. Пересчитывает контракты, полноту,
времена/разности/возмущения непосредственно из файлов; сохраняет все знаменатели.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

YEAR = 31557600.0
CONFIG = 'configs/experiments/W2_phase3_convergence_v1.yaml'
CASES = 'data/manifests/W2_phase3_cases_v1.json'
ANALYSIS = 'results/evidence/W2_phase3_analysis_20261028_pi/analysis.json'
DETERMINED = {'survived', 'host_contact', 'other_contact', 'operational_escape'}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'), default=float).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Повтор ключа JSON: ' + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError('Неконечная константа: ' + value)

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def read(path):
    return strict_loads(Path(path).read_text(encoding='utf-8'))


def jsonl(path):
    raw = Path(path).read_bytes()
    if not raw.endswith(b'\n'):
        raise ValueError('Оборванный JSONL: ' + str(path))
    return [strict_loads(line) for line in raw.decode('utf-8').splitlines() if line.strip()]


def time_of(row):
    if row.get('event'):
        return float(row['event']['time'])
    return row['years'] * YEAR if row['outcome'] == 'survived' else row['last_valid_time']


def known(row):
    return row['run_status'] == 'completed' and row['outcome'] in DETERMINED


def mode(cfg, code):
    return {k: float(v) for k, v in cfg['modes'][code].items() if k != 'family'}


def expected_specs(cfg, cases, block, b_years):
    result = []
    for case in cases:
        window = case.get('dense_window_years', cfg['dense_window_years_default'])
        if block != 'perturb' and case['block'] == block:
            for code in case['codes']:
                years = b_years.get(case['orbit_id'], cfg['horizon_years']) if code.startswith('B') else cfg['horizon_years']
                result.append(dict(orbit_id=case['orbit_id'], code=code, years=float(years),
                                   dense_window_years=float(window), perturbation=None))
        if block == 'perturb' and case.get('perturb_family'):
            p = cfg['perturbation']
            for scale in p['scales']:
                for direction in p['directions']:
                    result.append(dict(orbit_id=case['orbit_id'], code=case['perturb_family'],
                        years=float(p['horizon_years']),
                        dense_window_years=float(window) if cfg['grid']['perturbation_dense'] else 0.0,
                        perturbation=dict(scale=float(scale), direction=direction, seed=p['isotropic_seed'])))
    return result


def fingerprint(spec, unit, cfg, impl):
    family = cfg['modes'][spec['code']]['family']
    return digest(dict(stand='W2-PH3-v1', orbit_id=spec['orbit_id'], unit=digest(unit),
        code=spec['code'], mode=mode(cfg, spec['code']), family=family, years=spec['years'],
        perturbation=spec['perturbation'], window=spec['dense_window_years'], grid=cfg['grid'],
        implementation=impl[family]))


def archive_fingerprint(unit, row, cfg, impl):
    code = row['variant']
    payload = dict(stand='P2-2-v1', orbit_id=unit['orbit_id'], seed=unit['seed'], state=unit['state'],
        gms=unit['gms'], figures=unit['figures'], radii=unit['radii'], hill_km=unit['hill_km'],
        escape_window=unit['escape_window'], period=unit['period_seconds'], years=float(row['years']),
        variant=code, mode=mode(cfg, 'T' if code == 'T' else 'D'), grid='0+geomspace31(1e-5H..H)',
        implementation=impl['B' if code == 'B' else 'compiled'])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=float).encode()).hexdigest()


def initial_state(unit, perturb):
    state = np.array(unit['state'], dtype=float)
    if not perturb:
        return state
    r, v = state[-6:-3].copy(), state[-3:].copy()
    d, scale = perturb['direction'], perturb['scale']
    a, na = unit['a_km'], 2 * math.pi / unit['period_seconds'] * unit['a_km']
    if d.startswith('I'):
        vectors = np.random.default_rng(perturb['seed']).standard_normal((int(d[1:]) + 1, 6))
        vector = vectors[-1] / np.linalg.norm(vectors[-1])
        state[-6:-3] += scale * a * vector[:3]
        state[-3:] += scale * na * vector[3:]
    else:
        axis = {'R': r, 'T': v, 'N': np.cross(r, v)}[d[1]]
        state[-6:-3] += (1 if d[0] == '+' else -1) * scale * a * axis / np.linalg.norm(axis)
    return state


def perturb_stats(base, rows, scales, p0):
    """Неопределённые исходы отделены от физических смен; spread и сдвиг не смешаны."""
    result = {}
    for scale in scales:
        selected = [r for r in rows if r['perturbation']['scale'] == scale]
        same = [r for r in selected if known(r) and known(base) and r['outcome'] == base['outcome']]
        times = [time_of(r) for r in same]
        result[f'{scale:g}'] = dict(n=len(selected),
            unique_directions=len({r['perturbation']['direction'] for r in selected}),
            outcomes=dict(Counter(r['outcome'] for r in selected)),
            unresolved_or_incomplete=sum(not known(r) for r in selected),
            physical_flips=sum(known(r) and known(base) and r['outcome'] != base['outcome'] for r in selected),
            max_abs_dt_p0=max((abs(t - time_of(base)) / p0 for t in times), default=None),
            same_outcome_range_p0=(max(times) - min(times)) / p0 if times else None)
    return result


def compare_dense(a, b, unit):
    common, ia, ib = np.intersect1d(a['times'], b['times'], return_indices=True)
    diff = a['probe'][ia] - b['probe'][ib]
    a0 = unit['a_km']
    na = 2 * math.pi * a0 / unit['period_seconds']
    delta = np.maximum(np.linalg.norm(diff[:, :3], axis=1) / a0,
                       np.linalg.norm(diff[:, 3:], axis=1) / na)
    return common, delta


def audit(root):
    root = Path(root)
    cfg = yaml.safe_load((root / CONFIG).read_text(encoding='utf-8'))
    cases_doc, old = read(root / CASES), read(root / ANALYSIS)
    inventory, failures, checks = {}, [], Counter()

    def record(path, expected=None):
        path = Path(path)
        key = path.relative_to(root).as_posix()
        value = sha(path)
        inventory[key] = dict(sha256=value, bytes=path.stat().st_size)
        if expected is not None:
            check('sha', value == expected, key)
        return value

    def check(kind, passed, detail):
        checks[kind] += 1
        if not passed:
            failures.append(dict(kind=kind, detail=detail))

    record(root / CONFIG, old['config_sha256'])
    record(root / CASES, old['cases_sha256'])
    record(root / ANALYSIS)
    record(root / 'docs/tasks/W2_phase3_convergence_plan_v1.md')
    record(root / 'src/submoon_research/diagnostics/convergence.py', cases_doc['convergence_sha256'])
    check('freeze_config', cases_doc['config_sha256'] == old['config_sha256'], CASES)
    acfg, bcfg = cfg['archives']['p2_2a'], cfg['archives']['b144']
    for name, expected in [('summary.json', acfg['summary_sha256']), ('jobs.jsonl', acfg['jobs_sha256'])]:
        record(root / acfg['run'] / name, expected)
    for name, key in [('manifest.json', 'manifest_sha256'), ('units.json', 'units_file_sha256'), ('jobs.jsonl', 'jobs_sha256')]:
        record(root / bcfg['run'] / name, bcfg[key])
    record(root / 'data/manifests/W2_B144_reference_v1.json', bcfg['reference_sha256'])
    archive = jsonl(root / acfg['run'] / 'jobs.jsonl')
    arch_impl = read(root / acfg['run'] / 'summary.json')['implementations']
    b_rows = {r['orbit_id']: r for r in jsonl(root / bcfg['run'] / 'jobs.jsonl') if r['years'] == 1000.0}
    b_units = {u['orbit_id']: u for u in read(root / bcfg['run'] / 'units.json')['units']}
    all_rows, all_units, arrays, runs, archive_identity = [], {}, {}, [], []
    for ref in old['runs']:
        run = root / ref['run']
        record(run / 'manifest.json', ref['manifest_sha256'])
        m = read(run / 'manifest.json')
        record(run / 'units.json', m['units_file_sha256'])
        for name in ('jobs.jsonl', 'identity.json', 'progress.json'):
            record(run / name)
        for name, expected in m['code_sha256'].items():
            record(root / name, expected)
        if m['benchmark']:
            record(root / m['benchmark']['path'], m['benchmark']['sha256'])
        check('freeze_run', m['cases_sha256'] == old['cases_sha256'] and
              m['config_sha256'] == old['config_sha256'] and m['block'] == ref['block'], ref['run'])
        check('b_version', m['implementations']['ias15'] == cfg['expected_b_implementation'], ref['run'])
        doc = read(run / 'units.json')
        legacy = hashlib.sha256(json.dumps([(u['orbit_id'], u['seed'], u['state']) for u in doc['units']],
                                          default=float).encode()).hexdigest()
        check('units_digest', legacy == doc['units_sha256'], ref['run'])
        units = {u['orbit_id']: u for u in doc['units']}
        check('unique_units', len(units) == len(doc['units']), ref['run'])
        for oid, unit in units.items():
            if oid in all_units:
                check('cross_run_unit', canonical(unit) == canonical(all_units[oid]), oid)
            if oid in b_units:
                check('b144_exact_unit', canonical(unit) == canonical(b_units[oid]), oid)
            if oid not in all_units:
                arc = [r for r in archive if r['orbit_id'] == oid]
                okay = bool(arc) and all(archive_fingerprint(unit, r, cfg, arch_impl) == r['job_fingerprint']
                    and unit['state'][-6:] == r['samples'][0][1] for r in arc)
                check('archive_unit_fingerprints', okay, oid)
                archive_identity.append(dict(orbit_id=oid, rows=len(arc), passed=okay))
            all_units[oid] = unit
        specs = expected_specs(cfg, cases_doc['cases'], m['block'], m['b_horizon_years'])
        expected = {fingerprint(s, units[s['orbit_id']], cfg, m['implementations']): s for s in specs}
        planned = {p['fingerprint']: {k: v for k, v in p.items() if k != 'fingerprint'} for p in m['plan']}
        check('plan_exact', expected == planned and len(planned) == len(m['plan']), ref['run'])
        rows = jsonl(run / 'jobs.jsonl')
        check('rows_exact', len(rows) == len(expected) and
              set(r['job_fingerprint'] for r in rows) == set(expected), ref['run'])
        check('identity_claim', read(run / 'identity.json')['passed'] is True, ref['run'])
        for row in rows:
            fp, oid = row['job_fingerprint'], row['orbit_id']
            spec, unit = expected[fp], units[oid]
            check('row_spec', all(row[k] == v for k, v in spec.items()), fp)
            check('row_completed', row['run_status'] == 'completed' and row['error'] is None, fp)
            check('row_units', row['period_seconds'] == unit['period_seconds'] and row['a_km'] == unit['a_km'], fp)
            horizon, last = row['years'] * YEAR, row['last_valid_time']
            check('valid_time', 0 <= last <= horizon and (row['outcome'] != 'survived' or last == horizon), fp)
            event, cp = row['event'], row['checkpoint']
            check('event_time', not event or event['time'] == last, fp)
            expected_event = {'host_contact': 'reference_contact', 'other_contact': 'reference_contact',
                              'operational_escape': 'operational_escape'}.get(row['outcome'])
            check('event_label', (event is None and expected_event is None) or
                  (event is not None and event['event'] == expected_event), fp)
            check('checkpoint', cp['time'] == last and cp['state'] == row['final_state'] and
                  len(cp['state']) == len(unit['state']) and np.isfinite(cp['state']).all() and
                  cp['escape'] is not None, fp)
            path = root / row['samples_file']
            check('sample_path', path.parent == run / 'samples' and path.stem == fp[:24], fp)
            record(path, row['samples_sha256'])
            with np.load(path, allow_pickle=False) as data:
                z = {key: data[key] for key in data.files}
            arrays[fp] = z
            t, probe, ft, full = z['times'], z['probe'], z['full_times'], z['full_state']
            check('sample_structure', set(z) == {'times', 'probe', 'full_times', 'full_state'} and
                  len(t) == row['samples_count'] and probe.shape == (len(t), 6) and
                  full.shape == (len(ft), len(unit['state'])) and all(np.isfinite(v).all() for v in z.values()) and
                  t[0] == 0 and np.all(np.diff(t) > 0) and t[-1] <= last, fp)
            grid = cfg['grid']
            log = np.r_[0.0, np.geomspace(grid['log_start_fraction'] * horizon, horizon, grid['log_points'])]
            full_grid = log.copy()
            if spec['dense_window_years']:
                step = grid['dense_step_p0'] * unit['period_seconds']
                end = min(spec['dense_window_years'] * YEAR, horizon, step * grid['dense_max_points'])
                full_grid = np.unique(np.r_[log, np.arange(0.0, end, step)])
            expected_t, expected_ft = full_grid[full_grid <= last], log[log <= last]
            check('sample_grid', t.shape == expected_t.shape and ft.shape == expected_ft.shape and
                  np.allclose(t, expected_t, rtol=3e-15, atol=0) and
                  np.allclose(ft, expected_ft, rtol=3e-15, atol=0), fp)
            indices = np.searchsorted(t, ft)
            check('sample_full_probe', np.array_equal(t[indices], ft) and
                  np.array_equal(full[:, -6:], probe[indices]), fp)
            start = initial_state(unit, spec['perturbation'])
            # Тригонометрия/нормы Windows/WSL могут расходиться на единицы ULP.
            check('sample_initial', np.allclose(full[0], start, rtol=3e-15, atol=1e-15) and
                  np.array_equal(full[0][:-6], np.array(unit['state'][:-6])), fp)
        all_rows.extend(rows)
        runs.append(dict(run=ref['run'], block=m['block'], rows=len(rows), planned=len(expected),
            outcomes=dict(Counter(r['outcome'] for r in rows)),
            samples=len(list((run / 'samples').glob('*.npz'))),
            cpu_seconds=sum(r['cpu'] for r in rows), worker_wall_seconds=sum(r['wall'] for r in rows)))
    results = []
    for c in cases_doc['cases']:
        oid, unit = c['orbit_id'], all_units[c['orbit_id']]
        rows = [r for r in all_rows if r['orbit_id'] == oid]
        ladder = {r['code']: r for r in rows if r['perturbation'] is None}
        pert = [r for r in rows if r['perturbation'] is not None]
        p0 = unit['period_seconds']
        times = {code: time_of(r) for code, r in ladder.items()}
        t, delta = compare_dense(arrays[ladder['Ts']['job_fingerprint']], arrays[ladder['U']['job_fingerprint']], unit)
        early = (t > 0) & (t <= YEAR)
        sig = float(delta[early].max()) if early.any() else None
        saved = next(x for x in old['cases'] if x['orbit_id'] == oid)
        recorded = saved['classification']['sigma_num']
        check('sigma_num', sig == recorded or (sig is not None and recorded is not None and
              math.isclose(sig, recorded, rel_tol=1e-10, abs_tol=1e-15)), oid)
        check('labels_times', all(saved['labels'][code] == (r['outcome'] if known(r) else
              'unresolved:' + r['unresolved_reason']) and saved['times_years'][code] == time_of(r) / YEAR
              for code, r in ladder.items()), oid)
        stats = perturb_stats(ladder[c['perturb_family']], pert, cfg['perturbation']['scales'], p0) if pert else None
        result = dict(orbit_id=oid, role=c['role'], high_e=c['high_e'], p0_seconds=p0,
            labels={code: r['outcome'] for code, r in ladder.items()}, times_years={k: v / YEAR for k, v in times.items()},
            dt_from_U_p0={k: (v - times['U']) / p0 for k, v in times.items()}, sigma_num=sig,
            perturbation=stats, archived_class=saved['classification']['cls'],
            archived_confidence=saved['classification']['confidence'])
        if oid in b_rows:
            result['b144'] = dict(outcome=b_rows[oid]['outcome'], time_years=time_of(b_rows[oid]) / YEAR,
                                 dt_from_U_p0=(time_of(b_rows[oid]) - times['U']) / p0)
        results.append(result)
    # Повтор P1 не расширяет знаменатель и не является независимой выборкой.
    previous = root / 'runs/W2-PH3-P1-20261026T120102Z'
    repeat = dict(available=previous.exists())
    if previous.exists():
        record(previous / 'jobs.jsonl')
        prior = jsonl(previous / 'jobs.jsonl')
        now = {r['job_fingerprint']: r for r in all_rows if r['perturbation'] is None}
        fields = ('outcome', 'event', 'last_valid_time', 'steps', 'nfev', 'final_state', 'checkpoint',
                  'grid_diagnostics', 'massive_energy_relative_drift', 'eccentricity', 'numerical_domain')
        mismatches = []
        for r in prior:
            other = now.get(r['job_fingerprint'])
            if other is None or any(r[k] != other[k] for k in fields):
                mismatches.append(r['job_fingerprint'])
            path = root / r['samples_file']
            record(path, r['samples_sha256'])
            with np.load(path, allow_pickle=False) as z:
                if other and any(not np.array_equal(z[k], arrays[r['job_fingerprint']][k]) for k in z.files):
                    mismatches.append(r['job_fingerprint'] + ':samples')
        repeat.update(rows=len(prior), compared_fields=list(fields), mismatches=mismatches)
        check('P1_repeat', len(prior) == 85 and not mismatches, previous.name)
    return dict(schema='w2-phase3-audit-v1', source_analysis=ANALYSIS, checks=dict(checks),
        failures=failures, integrity_passed=not failures, runs=runs, cases=results,
        archive_identity=archive_identity, previous_p1=repeat, inventory=inventory,
        note='Без интеграций. Совпадение SHA с записью не доказывает корректность физики; текущие SHA JSONL зафиксированы аудитом.')
