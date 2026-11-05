"""Read-only, post-hoc B144 analysis. No integrations or production admission.

Trajectory comparisons use exact common saved times only; absence of a sampled
threshold crossing does not prove its absence between samples. High-e and
computational failures remain in full denominators, separate from physical losses.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

YEAR = 31557600.0
MANIFEST_SHA = 'eaf17a3b48b3722b098309fe738283bf5f06db34493a665650043fe856151205'
ARCHIVE_SHA = '0c17c36ac24b7bf0f8cd6ab939ef8464e3a51daf82265ab1f637b6a5fef41175'
CATEGORIES = ('survived', 'host_contact', 'other_contact', 'operational_escape', 'unresolved')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def key(row):
    return row['orbit_id'], float(row['years'])


def index_rows(rows, include_variant=False):
    result = {}
    for row in rows:
        k = (*key(row), row['variant']) if include_variant else key(row)
        if k in result:
            raise ValueError(f'duplicate key {k}')
        result[k] = row
    return result


def category(row):
    if row['run_status'] != 'completed':
        return 'unresolved'
    return row['outcome']


def label(row):
    return category(row), row.get('unresolved_reason'), row['run_status']


def high_e(row):
    return str(row.get('unresolved_reason') or '').startswith('high_e')


def quantiles(values):
    values = [float(v) for v in values if v is not None and math.isfinite(v)]
    if not values:
        return {'n': 0, 'min': None, 'p10': None, 'median': None, 'p90': None, 'max': None}
    return dict(zip(('min', 'p10', 'median', 'p90', 'max'),
                    map(float, np.quantile(values, [0, .1, .5, .9, 1])), strict=True), n=len(values))


def outcome_counts(rows):
    c = Counter(category(r) for r in rows)
    n = len(rows)
    return dict(n=n, counts={s: c[s] for s in CATEGORIES},
                high_e=sum(high_e(r) for r in rows),
                computational=sum(r['run_status'] != 'completed' for r in rows),
                survival_identification_bounds=([c['survived']/n, (c['survived']+c['unresolved'])/n]
                                                if n else None))


def sample_comparison(new, other, unit, threshold=1e-6):
    """First *observed* crossing, not Lyapunov time or a continuous-time bracket."""
    a = unit['a_km']
    speed = 2*math.pi*a/unit['period_seconds']
    old = {float(t): np.asarray(s) for t, s in other['samples']}
    values = []
    for t, state in new['samples']:
        if float(t) not in old:
            continue
        x, y = np.asarray(state), old[float(t)]
        dr, dv = float(np.linalg.norm(x[:3]-y[:3])/a), float(np.linalg.norm(x[3:]-y[3:])/speed)
        radial = abs(float(np.linalg.norm(x[:3])-np.linalg.norm(y[:3])))/a
        values.append((float(t), dr, dv, radial))
    crossing = next((i for i, v in enumerate(values) if max(v[1:3]) > threshold), None)
    return dict(common_samples=len(values), last_common_years=values[-1][0]/YEAR if values else None,
                first_observed_crossing_years=values[crossing][0]/YEAR if crossing is not None else None,
                previous_saved_time_years=values[crossing-1][0]/YEAR if crossing not in (None, 0) else None,
                max_dr_over_a=max((v[1] for v in values), default=None),
                max_dv_over_na=max((v[2] for v in values), default=None),
                max_radial_difference_over_a=max((v[3] for v in values), default=None),
                threshold=threshold, continuous_crossing_known=False)


def event_comparison(new, other, period):
    e, f = new.get('event'), other.get('event')
    if not e or not f or (e['event'], e['body']) != (f['event'], f['body']):
        return None
    return dict(event=e['event'], body=e['body'], new_time_years=e['time']/YEAR,
                other_time_years=f['time']/YEAR, signed_dt_years=(e['time']-f['time'])/YEAR,
                abs_dt_over_P0=abs(e['time']-f['time'])/period)


def pair_stats(pairs, units):
    resolved = [(n, o) for n, o in pairs if n['run_status'] == o['run_status'] == 'completed'
                and category(n) != 'unresolved' and category(o) != 'unresolved']
    events, trajectories = [], []
    for new, other in pairs:
        u = units[new['orbit_id']]
        e = event_comparison(new, other, u['period_seconds'])
        if e is not None:
            events.append(e)
        trajectories.append(sample_comparison(new, other, u))
    return dict(n=len(pairs), discordant=sum(label(n) != label(o) for n, o in pairs),
                transitions=dict(sorted(Counter(f'{category(o)}->{category(n)}' for n, o in pairs).items())),
                resolved_pairs=len(resolved), resolved_discordant=sum(label(n) != label(o) for n, o in resolved),
                other_high_e=sum(high_e(o) for _, o in pairs),
                other_failed=sum(o['run_status'] != 'completed' for _, o in pairs),
                event_pairs=len(events), event_dt_P0=quantiles(e['abs_dt_over_P0'] for e in events),
                event_over_1e6=sum(e['abs_dt_over_P0'] > 1e-6 for e in events),
                event_over_1period=sum(e['abs_dt_over_P0'] > 1 for e in events),
                tau_years=quantiles(t['first_observed_crossing_years'] for t in trajectories),
                no_observed_crossing=sum(t['first_observed_crossing_years'] is None for t in trajectories),
                sample_coverage_years=quantiles(t['last_common_years'] for t in trajectories))


def sampled_orbital(row, gm):
    """Osculating two-body diagnostics of saved host-relative probe, not invariants."""
    es, bound_es, energies, rs = [], [], [], []
    for _, state in row['samples']:
        x = np.asarray(state)
        r, v = x[:3], x[3:]
        radius = float(np.linalg.norm(r))
        if radius <= 0:
            continue
        energy = float(v @ v)/2-gm/radius
        ecc = float(np.linalg.norm(np.cross(v, np.cross(r, v))/gm-r/radius))
        es.append(ecc)
        if energy < 0:
            bound_es.append(ecc)
        energies.append(energy)
        rs.append(radius)
    return dict(sample_count=len(es), max_e_sampled=max(es, default=None),
                max_bound_e_sampled=max(bound_es, default=None),
                bound_high_e_samples=sum(e >= .9 for e in bound_es),
                min_radius_km_sampled=min(rs, default=None),
                two_body_energy_range_km2_s2=[min(energies), max(energies)] if energies else None)


def independently_validate(new, manifest, units, archive, reference) -> dict[str, Any]:
    """Independent row validation including rejected unknown statuses/events."""
    plan = {(p['orbit_id'], float(p['years'])) for p in manifest['plan']}
    if len(plan) != 288 or len(manifest['plan']) != 288 or set(new) != plan or len(units) != 144:
        raise ValueError('plan/units/rows mismatch')
    references = index_rows(reference['jobs'])
    for k, row in new.items():
        u = units[k[0]]
        expected = fingerprint(u, k[1], 'B', manifest['b_implementation'])
        if row['job_fingerprint'] != expected:
            raise ValueError('new fingerprint mismatch')
        for variant in ('B', 'D', 'T'):
            old = archive[(*k, variant)]
            implementation = manifest['archived_implementations']['B' if variant == 'B' else 'compiled']
            if old['job_fingerprint'] != fingerprint(u, k[1], variant, implementation):
                raise ValueError('archive fingerprint mismatch')
            if old['samples'][0][1] != u['state'][-6:]:
                raise ValueError('archive initial probe mismatch')
        if references[k]['job_fingerprint'] != archive[(*k, 'B')]['job_fingerprint']:
            raise ValueError('reference mismatch')
        if row['run_status'] != 'completed' or row['error'] is not None or row['stopped']:
            raise ValueError('not a completed error-free row')
        t, event = row['last_valid_time'], row['event']
        if not 0 <= t <= row['years']*YEAR:
            raise ValueError('invalid time')
        if event is None:
            if t != row['years']*YEAR or row['outcome'] not in ('survived', 'unresolved'):
                raise ValueError('horizon/outcome mismatch')
        else:
            if event['time'] != t:
                raise ValueError('event time mismatch')
            expected_outcome = ('operational_escape' if event['event'] == 'operational_escape'
                                else 'host_contact' if event['event'] == 'reference_contact' and event['body'] == 0
                                else 'other_contact' if event['event'] == 'reference_contact' else None)
            if expected_outcome != row['outcome']:
                raise ValueError('event outcome mismatch')
        if row['samples'][0] != [0.0, u['state'][-6:]]:
            raise ValueError('new initial probe mismatch')
        times = [s[0] for s in row['samples']]
        if times != sorted(set(times)) or times[-1] > t:
            raise ValueError('sample times invalid')
        if not np.isfinite(row['final_probe']).all() or not all(np.isfinite(s).all() for _, s in row['samples']):
            raise ValueError('nonfinite probe')
        if row['steps'] != row['b_diagnostics']['steps'] or row['b_diagnostics']['diagnostic_error']:
            raise ValueError('diagnostics mismatch')
    return dict(passed=True, new_fingerprints=288, archived_fingerprints=864,
                initial_probes_checked=1152, exact_manifest=True, unknown_status_allowed=False)


def fingerprint(u, years, variant, implementation):
    mode = dict(rtol=1e-11, atol_position=1e-8, atol_velocity=1e-11,
                step_fraction=.025, epsilon=1e-9, cache_epsilon=1e-12)
    if variant == 'T':
        mode = dict(rtol=1e-13, atol_position=1e-10, atol_velocity=1e-13,
                    step_fraction=.0125, epsilon=1e-12, cache_epsilon=1e-13)
    payload = dict(stand='P2-2-v1', orbit_id=u['orbit_id'], seed=u['seed'], state=u['state'],
                   gms=u['gms'], figures=u['figures'], radii=u['radii'], hill_km=u['hill_km'],
                   escape_window=u['escape_window'], period=u['period_seconds'], years=float(years),
                   variant=variant, mode=mode, grid='0+geomspace31(1e-5H..H)', implementation=implementation)
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=float).encode()).hexdigest()


def analyze(root):
    root = Path(root)
    run = root/'runs/W2-B144-57496493'
    old_run = root/'runs/W2-P2-2a-56220810'
    manifest = read_json(run/'manifest.json')
    if digest(run/'manifest.json') != MANIFEST_SHA or digest(old_run/'jobs.jsonl') != ARCHIVE_SHA:
        raise ValueError('pinned source hash mismatch')
    if digest(run/'units.json') != manifest['units_file_sha256']:
        raise ValueError('units hash mismatch')
    summary = read_json(run/'summary.json')
    if digest(run/'jobs.jsonl') != summary['jobs_file_sha256']:
        raise ValueError('jobs hash mismatch')
    reference_path = root/'data/manifests/W2_B144_reference_v1.json'
    if digest(reference_path) != manifest['reference_sha256']:
        raise ValueError('reference hash mismatch')
    reference = read_json(reference_path)
    rows = [json.loads(line) for line in (run/'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    old_rows = [json.loads(line) for line in (old_run/'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    new, old = index_rows(rows), index_rows(old_rows, True)
    unit_list = read_json(run/'units.json')['units']
    units = {u['orbit_id']: u for u in unit_list}
    integrity = independently_validate(new, manifest, units, old, reference)
    integrity['code_hashes_match_current'] = {p: digest(root/p) == h for p, h in manifest['code_sha256'].items()}
    cells = sorted({(r['host'], r['measure']) for r in rows})
    outcome_tables, pairs_table, full_archive = [], [], []
    for years in (100., 1000.):
        for host, measure in [(None, None), *[(h, None) for h in sorted({r['host'] for r in rows})], *cells]:
            selected = [r for r in rows if r['years'] == years and (host is None or r['host'] == host)
                        and (measure is None or r['measure'] == measure)]
            for name in ('new_B', 'old_B', 'D', 'T'):
                rr = selected if name == 'new_B' else [old[(*key(r), 'B' if name == 'old_B' else name)] for r in selected]
                outcome_tables.append(dict(years=years, host=host, measure=measure, variant=name, **outcome_counts(rr)))
            for variant in ('B', 'D', 'T'):
                pp = [(r, old[(*key(r), variant)]) for r in selected]
                pairs_table.append(dict(years=years, host=host, measure=measure, other=variant,
                                        **pair_stats(pp, units)))
        for variant in ('D', 'T'):
            rr = [r for r in old_rows if r['years'] == years and r['variant'] == variant]
            full_archive.append(dict(years=years, variant=variant, **outcome_counts(rr)))
    # Every discordant identity, including failed old B; event timings kept separately.
    dossier, event_rows, high_rows = [], [], []
    special_ids = set()
    for k in sorted(new):
        r, u = new[k], units[k[0]]
        differences = [v for v in ('B', 'D', 'T') if label(r) != label(old[(*k, v)])]
        detail = dict(orbit_id=k[0], years=k[1], host=u['host'], measure=u['measure'],
                      a_km=u['a_km'], a_over_rh=u['a_over_rh'], initial_e=u['eccentricity'],
                      initial_cos_i=u['cos_i'], period_days=u['period_seconds']/86400,
                      new_outcome=r['outcome'], new_event=r['event'],
                      new_last_years=r['last_valid_time']/YEAR, new_wall_s=r['wall'],
                      new_sampled_orbital=sampled_orbital(r, u['gms'][0]), comparisons={})
        for variant in ('B', 'D', 'T'):
            o = old[(*k, variant)]
            ev = event_comparison(r, o, u['period_seconds'])
            detail['comparisons'][variant] = dict(outcome=o['outcome'], reason=o.get('unresolved_reason'),
                status=o['run_status'], last_years=o['last_valid_time']/YEAR, event=o['event'],
                wall_s=o['wall'], eccentricity=o.get('eccentricity'),
                trajectory=sample_comparison(r, o, u), event_comparison=ev)
            if ev is not None:
                event_rows.append(dict(orbit_id=k[0], years=k[1], other=variant, **ev))
            if high_e(o):
                high_rows.append(dict(orbit_id=k[0], years=k[1], other=variant,
                    stop_years=o['last_valid_time']/YEAR, new_outcome=r['outcome'],
                    new_event_years=r['event']['time']/YEAR if r['event'] else None,
                    new_wall_s=r['wall'], remaining_horizon_years=k[1]-o['last_valid_time']/YEAR))
        if differences:
            special_ids.add(k[0])
            dossier.append(dict(detail, differing_variants=differences))
    # Independent horizons: same equations/start, but clipping at H affects adaptive history.
    horizon_checks = []
    for oid, u in units.items():
        a, b = new[(oid, 100.)], new[(oid, 1000.)]
        a_event, b_event = a['event'], b['event']
        prefix_event_consistent = (not a_event or (b_event is not None
                                  and (a_event['event'], a_event['body']) == (b_event['event'], b_event['body'])))
        horizon_checks.append(dict(orbit_id=oid, short_outcome=a['outcome'], long_outcome=b['outcome'],
            same_prefix_event_type=prefix_event_consistent,
            event_dt_s=abs(a_event['time']-b_event['time']) if a_event and b_event else None,
            common_samples=sample_comparison(a, b, u),
            long_event_before_short_horizon=(b_event is not None and b_event['time'] <= 100*YEAR)))
    small_rows = []
    for r in rows:
        d = r['b_diagnostics']
        if d['small_step_events']:
            small_rows.append(dict(orbit_id=r['orbit_id'], years=r['years'], outcome=r['outcome'],
                wall_s=r['wall'], steps=r['steps'], small_step_count=d['small_step_events'],
                min_dt_over_max_step=d['min_dt_over_max_step'], minimum_context=d['min_dt_over_max_step_at'],
                min_dt_over_ulp_t=d['min_dt_over_ulp_t'], min_cancellation_ratio=d['min_cancellation_ratio'],
                saved_small_steps=d['events']))
    diagnostic = dict(total_steps=sum(r['steps'] for r in rows),
        small_step_count=sum(r['b_diagnostics']['small_step_events'] for r in rows),
        jobs_with_small_steps=len(small_rows), unique_units_with_small_steps=len({r['orbit_id'] for r in small_rows}),
        min_dt_over_ulp_t=quantiles(r['b_diagnostics']['min_dt_over_ulp_t'] for r in rows),
        min_interior_dt_fraction=quantiles(r['b_diagnostics']['min_dt_over_max_step'] for r in rows),
        max_endpoint_residual=quantiles(r['b_diagnostics']['max_endpoint_residual'] for r in rows),
        sampled_max_abs_coordinate_km=quantiles(r['b_diagnostics']['max_abs_coordinate_km'] for r in rows),
        at_max_step=sum(r['b_diagnostics']['steps_at_max_step'] for r in rows),
        energy_drift_saved=any('massive_energy_relative_drift' in r for r in rows),
        full_final_state_saved=any('final_state' in r or 'checkpoint' in r for r in rows))
    cost_cells = []
    for years in (100., 1000.):
        for h, m in [(None, None), *cells]:
            rr = [r for r in rows if r['years'] == years and (h is None or r['host'] == h)
                  and (m is None or r['measure'] == m)]
            cost_cells.append(dict(years=years, host=h, measure=m, jobs=len(rr),
                wall_worker_hours=sum(r['wall'] for r in rr)/3600, cpu_hours=sum(r['cpu'] for r in rr)/3600,
                wall_seconds=quantiles(r['wall'] for r in rr), steps=sum(r['steps'] for r in rr)))
    ranked = sorted(rows, key=lambda r: -r['wall'])
    total = sum(r['wall'] for r in rows)
    paired_cost = []
    for r in rows:
        o = old[(*key(r), 'B')]
        if (o['run_status'] == 'completed' and r['wall'] > 1 and o['wall'] > 1
                and r['last_valid_time'] == o['last_valid_time'] and category(r) == category(o)):
            paired_cost.append(dict(orbit_id=r['orbit_id'], years=r['years'], ratio=r['wall']/o['wall'],
                                    steps_ratio=r['steps']/o['steps'], old_wall=o['wall'], new_wall=r['wall']))
    cost = dict(cpu_hours=sum(r['cpu'] for r in rows)/3600, worker_hours=total/3600,
        wall_hours=summary['wall_seconds']/3600, workers=summary['workers'],
        mean_active_workers=total/summary['wall_seconds'],
        allocated_worker_utilization=total/(summary['wall_seconds']*summary['workers']),
        top1_fraction=ranked[0]['wall']/total, top10_fraction=sum(r['wall'] for r in ranked[:10])/total,
        longest=[dict(orbit_id=r['orbit_id'], years=r['years'], outcome=r['outcome'],
                      wall_hours=r['wall']/3600, steps=r['steps']) for r in ranked[:12]],
        same_endpoint_cost_ratios=quantiles(p['ratio'] for p in paired_cost),
        same_endpoint_step_ratios=quantiles(p['steps_ratio'] for p in paired_cost),
        same_endpoint_pairs=paired_cost,
        archived_b_worker_hours=sum(r['wall'] for r in old_rows if r['variant'] == 'B')/3600,
        benchmark_prediction_hours=read_json(run/'benchmark.json')['predicted_wall_s']/3600)
    # Whole archive D/T disagreements are not recomputed integrations, nor replaced with B labels.
    whole_discord = []
    for r in old_rows:
        if r['variant'] != 'D' or r['years'] != 1000:
            continue
        other = old[(*key(r), 'T')]
        if label(r) != label(other):
            whole_discord.append(dict(orbit_id=r['orbit_id'], D=label(r), T=label(other),
                                     B_available=key(r) in new))
    source_paths = [run/'manifest.json', run/'units.json', run/'jobs.jsonl', run/'summary.json',
                    old_run/'jobs.jsonl', old_run/'summary.json', reference_path]
    return dict(schema='b144-scientific-analysis-v1', created_utc=datetime.now(timezone.utc).isoformat(),
        integrations=0, scientific_admission=False, analysis_design='post_hoc_descriptive',
        sources={p.relative_to(root).as_posix(): digest(p) for p in source_paths}, integrity=integrity,
        outcomes=outcome_tables, pairs=pairs_table, archive_full=full_archive,
        discordant_dossiers=dossier, discordant_unique_units=len(special_ids),
        event_pairs=sorted(event_rows, key=lambda r: -r['abs_dt_over_P0']), high_e_resolution=high_rows,
        horizon_checks=horizon_checks, diagnostics=diagnostic, small_step_jobs=small_rows,
        cost=cost, cost_cells=cost_cells, full_DT_discordance_1000=whole_discord,
        unique_initial_units=len(units), jobs=len(rows),
        hypothetical_iid_zero_failure_upper95={str(n): 1-.05**(1/n) for n in (16, 48, 144, 288)},
        hypothetical_iid_n_for_half_percent=math.ceil(math.log(.05)/math.log(1-.005)))
