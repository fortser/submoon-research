"""W2-T066: отдельный пересчёт сырых данных; без интеграций и изменения runs.

Функции прежних анализаторов не используются для пересчёта. Только синтетический
контрпример намеренно вызывает v1/v2, чтобы проверить их защитные ограничения.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.diagnostics import convergence as v1  # noqa: E402
from submoon_research.diagnostics import convergence_v2_guard as v2  # noqa: E402

OUT = ROOT / 'results/evidence/W2_T066_quality_20261030/independent.json'
YEAR = 31557600.0
KNOWN = {'survived', 'host_contact', 'other_contact', 'operational_escape'}
SOURCES: dict[str, str] = {}


def read(path: str) -> Any:
    raw = (ROOT / path).read_bytes()
    SOURCES[path] = hashlib.sha256(raw).hexdigest()

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f'Повтор JSON-ключа: {key}')
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f'Неконечное число: {value}')

    def parse(text):
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)

    if path.endswith('.jsonl'):
        assert raw.endswith(b'\n')
        return [parse(line) for line in raw.decode('utf-8').splitlines() if line.strip()]
    return parse(raw.decode('utf-8'))


def known(row):
    return row['run_status'] == 'completed' and row['outcome'] in KNOWN


def time_of(row):
    if row['event'] is not None:
        return row['event']['time']
    return row['years'] * YEAR if row['outcome'] == 'survived' else row['last_valid_time']


def close(a, b):
    # Только сравнение повторной арифметики; не допуск научной модели.
    return a == b or (a is not None and b is not None and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12))


def synthetic_k2(thr, cfg):
    """Неизвестный исход D не должен доказывать чувствительность физического события."""
    def row(outcome, time):
        return dict(run_status='completed', outcome=outcome, years=1000., last_valid_time=time,
                    event=None if outcome == 'unresolved' else dict(event='operational_escape', time=time),
                    unresolved_reason='high_e_prolonged' if outcome == 'unresolved' else None)

    rows = {code: row('operational_escape', 120.) for code in ('D', 'Ds', 'T', 'Ts', 'U')}
    rows['D'] = row('unresolved', 115.)
    # Общая сетка не выходит за последнее допустимое время ни одной строки.
    probe = np.array([[1., 0., 0., 0., 1., 0.], [1., 0., 0., 0., 1., 0.]])
    dense = {code: (np.array([0., 110.]), probe.copy()) for code in rows}
    perturb = [dict(copy.deepcopy(rows['Ts']), perturbation=dict(scale=s, direction=d))
               for s in cfg['perturbation']['scales'] for d in cfg['perturbation']['directions']]
    case = dict(rows=rows, dense=dense, ladders=cfg['ladders'], p0=1., a0=1., n0=1.,
                perturb_family='Ts', perturb_rows=perturb, scales=cfg['perturbation']['scales'], high_e=False)
    old = v1.classify(case, thr)
    new = v2.classify(case, thr, cfg['perturbation']['directions'])
    assert old['cls'] == new['cls'] == 'K2'
    return dict(synthetic=True, coarse_outcome='unresolved', last_valid_time=115., event_time=120.,
                common_times=[0., 110.], v1_class=old['cls'], v2_class=new['cls'],
                k2=old['k2'], physical_event_sensitivity_established=False,
                limitation='Прямой API классификатора; это не новая орбита и не результат production.')


def main():
    if OUT.exists():
        raise SystemExit('Выход существует: нужен отдельный новый артефакт')
    base = 'results/evidence/W2_T066_quality_20261030/'
    cfg_path = 'configs/experiments/W2_phase3_convergence_v1.yaml'
    raw = (ROOT / cfg_path).read_bytes()
    SOURCES[cfg_path] = hashlib.sha256(raw).hexdigest()
    cfg = yaml.safe_load(raw)
    historical = read('results/evidence/W2_phase3_analysis_20261028_pi/analysis.json')
    comparison = read(base + 'comparison/analysis.json')
    original_v2 = read('results/evidence/W2_T065_v1_v2_20261030_v1/analysis.json')
    assert comparison['cases'] == original_v2['cases']
    assert comparison['transitions'] == original_v2['transitions']
    old_b = read('results/evidence/W2_B144_scientific_20261024/v1/analysis.json')
    new_b = read(base + 'b144.json')
    b_equal = {key: old_b[key] == new_b[key] for key in old_b if key != 'created_utc'}
    assert all(b_equal.values())
    audit = read(base + 'phase3/audit.json')
    old_audit = read('results/evidence/W2_phase3_audit_20261030/v1/audit.json')
    assert audit['inventory'] == old_audit['inventory']
    all_rows = []
    blocks = []
    for ref in historical['runs']:
        rows = read(ref['run'] + '/jobs.jsonl')
        all_rows.extend(rows)
        blocks.append(dict(block=ref['block'], rows=len(rows), outcomes=dict(Counter(r['outcome'] for r in rows))))
    assert len(all_rows) == len({r['job_fingerprint'] for r in all_rows}) == 439
    cases = []
    for saved, revised in zip(historical['cases'], comparison['cases'], strict=True):
        oid = saved['orbit_id']
        assert oid == revised['orbit_id']
        mine = [r for r in all_rows if r['orbit_id'] == oid]
        ladder = {r['code']: r for r in mine if r['perturbation'] is None}
        pert = [r for r in mine if r['perturbation'] is not None]
        p0 = mine[0]['period_seconds']
        for code, row in ladder.items():
            assert close(time_of(row) / YEAR, saved['times_years'][code])
        groups = {}
        for s in cfg['perturbation']['scales']:
            selected = [r for r in pert if r['perturbation']['scale'] == s]
            known_rows = [r for r in selected if known(r)]
            by_type: dict[str, list[float]] = {}
            for row in known_rows:
                by_type.setdefault(row['outcome'], []).append(time_of(row))
            family = pert[0]['code'] if pert else None
            baseline = ladder.get(family)
            flips = sum(r['outcome'] != baseline['outcome'] for r in known_rows) if baseline and known(baseline) else None
            ranges = {k: (max(v) - min(v)) / p0 for k, v in by_type.items()}
            shifts = [abs(time_of(r) - time_of(baseline)) / p0 for r in known_rows
                      if baseline and known(baseline) and r['outcome'] == baseline['outcome']]
            spread, shift = max(ranges.values(), default=None), max(shifts, default=None)
            rec = revised['physical_sensitivity'][f'{s:g}']
            assert rec['unknown'] == len(selected) - len(known_rows)
            assert rec['physical_flips'] == flips
            assert close(rec['spread_p0'], spread) and close(rec['max_shift_p0'], shift)
            groups[f'{s:g}'] = dict(n=len(selected), unknown=rec['unknown'], physical_flips=flips,
                                     range_by_outcome_p0=ranges, spread_p0=spread, max_shift_p0=shift)
        k2 = []
        for diagnostic in saved['classification']['k2'] if 'k2' in saved['classification'] else []:
            a, b = (ladder[k] for k in diagnostic['pair'])
            arrays = []
            for row in (a, b):
                path = row['samples_file']
                raw = (ROOT / path).read_bytes()
                SOURCES[path] = hashlib.sha256(raw).hexdigest()
                assert SOURCES[path] == row['samples_sha256']
                with np.load(ROOT / path, allow_pickle=False) as z:
                    arrays.append((z['times'], z['probe']))
            t, ia, ib = np.intersect1d(arrays[0][0], arrays[1][0], return_indices=True)
            diff = arrays[0][1][ia] - arrays[1][1][ib]
            delta = np.maximum(np.linalg.norm(diff[:, :3], axis=1) / a['a_km'],
                               np.linalg.norm(diff[:, 3:], axis=1) / (2 * math.pi * a['a_km'] / p0))
            events = [r['event']['time'] for r in (a, b) if r['event']]
            event_time = min(events)
            hits = np.flatnonzero(delta > 1e-6)
            tau = float(t[hits[0]]) if len(hits) else None
            covered = bool(np.any((t >= event_time - 10 * p0) & (t <= event_time)))
            assert covered == diagnostic['covered'] and close(tau, diagnostic['tau_1e6'])
            k2.append(dict(pair=diagnostic['pair'], covered=covered, tau_1e6=tau,
                           gap_to_last_common_p0=float((event_time - t[t <= event_time][-1]) / p0)))
        cases.append(dict(orbit_id=oid, perturbation=groups, k2=k2))
    bpath = 'runs/W2-B144-57496493/'
    b_rows = read(bpath + 'jobs.jsonl')
    archive = read('runs/W2-P2-2a-56220810/jobs.jsonl')
    units = {u['orbit_id']: u for u in read(bpath + 'units.json')['units']}
    indexed = {(r['orbit_id'], r['years'], r['variant']): r for r in archive}
    b_counts = []
    for years in (100., 1000.):
        selected = [r for r in b_rows if r['years'] == years]
        pairs = {}
        for code in ('B', 'D', 'T'):
            pp = [(r, indexed[(r['orbit_id'], years, code)]) for r in selected]
            determinate = [(a, b) for a, b in pp if known(a) and known(b)]
            pairs[code] = dict(determined=len(determinate), different=sum(a['outcome'] != b['outcome'] for a, b in determinate))
        b_counts.append(dict(years=years, outcomes=dict(Counter(r['outcome'] for r in selected)), pairs=pairs))
    assert b_counts[1]['pairs']['T'] == dict(determined=138, different=0)
    assert b_counts[1]['pairs']['D'] == dict(determined=139, different=2)
    residuals = [abs(float(np.linalg.norm(r['final_probe'][:3])) - units[r['orbit_id']]['radii'][0])
                 for r in b_rows if r['outcome'] == 'host_contact']
    result = dict(schema='w2-t066-independent-v1', integrations=0, production_allowed=False,
                  blocks=blocks, outcomes=dict(Counter(r['outcome'] for r in all_rows)),
                  base_orbits=len(cases), replay_b144_exact_except_utc=b_equal,
                  inventory_unchanged_files=len(audit['inventory']), replay_v2_cases_exact=True,
                  cases=cases, b144=b_counts, b144_steps=sum(r['steps'] for r in b_rows),
                  b144_worker_hours=sum(r['wall'] for r in b_rows) / 3600.,
                  b144_contact_max_residual_km=max(residuals), synthetic_k2=synthetic_k2(cfg['thresholds'], cfg))
    # Обнаружить изменение прочитанных источников во время проверки.
    for path, expected in SOURCES.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
    result['sources_sha256'] = SOURCES
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    with OUT.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(output=OUT.relative_to(ROOT).as_posix(), blocks=blocks,
                          k2_candidates=sum(len(c['k2']) for c in cases), synthetic_k2=result['synthetic_k2']),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
