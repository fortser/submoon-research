"""W2-T064: проверки выводов аудита/повторного анализа, без интеграций и изменения данных."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from submoon_research.diagnostics.phase3_audit import (  # noqa: E402
    ANALYSIS, CASES, YEAR, jsonl, read, sha, time_of,
)


def differences(a, b, path='') -> list[dict[str, Any]]:
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return [x for k in a for x in differences(a[k], b[k], path + '/' + k)]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i, (aa, bb) in enumerate(zip(a, b)) for x in differences(aa, bb, path + '/' + str(i))]
    return [] if a == b else [dict(path=path, old=a, new=b)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', required=True)
    parser.add_argument('--replay', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise SystemExit('Результат уже существует')
    audit, original, replay = read(args.audit), read(ROOT / ANALYSIS), read(args.replay)
    checks = {}
    checks['inventory_unchanged'] = all(sha(ROOT / p) == v['sha256'] for p, v in audit['inventory'].items())
    checks['classifications_exact'] = all(a['classification'] == b['classification']
                                         for a, b in zip(original['cases'], replay['cases']))
    checks['decision_exact'] = original['decision'] == replay['decision']
    diff = differences(original['cases'], replay['cases'])
    checks['replay_only_descriptive_fit_roundoff'] = all('/growth/' in d['path'] and
        isinstance(d['old'], float) and isinstance(d['new'], float) and
        abs(d['old'] - d['new']) <= 2e-14 * max(abs(d['old']), abs(d['new'])) for d in diff)
    p2 = jsonl(ROOT / 'runs/W2-P2-2a-56220810/jobs.jsonl')
    b144 = {r['orbit_id']: r for r in jsonl(ROOT / 'runs/W2-B144-57496493/jobs.jsonl') if r['years'] == 1000.0}
    units = {u['orbit_id']: u for u in read(ROOT / 'runs/W2-B144-57496493/units.json')['units']}
    dt = {(r['orbit_id'], r['variant']): r for r in p2 if r['years'] == 1000.0 and r['variant'] in ('D', 'T')}
    frozen = read(ROOT / CASES)
    excluded = {c['orbit_id'] for c in frozen['cases'] if c['role'] == 'target'}
    excluded.update(o for group in frozen['deferred'].values() for o in group)
    groups = {}
    for oid, b in b144.items():
        d, t = dt[(oid, 'D')], dt[(oid, 'T')]
        if oid in excluded or b['wall'] > 300 or b['outcome'] not in ('survived', 'host_contact', 'operational_escape'):
            continue
        if not b['outcome'] == d['outcome'] == t['outcome']:
            continue
        times = [time_of(r) for r in (b, d, t)]
        if b['outcome'] != 'survived' and max(times) - min(times) > units[oid]['period_seconds']:
            continue
        key = (b['wall'], b['unit']) if b['outcome'] == 'survived' else (-time_of(b), b['unit'])
        groups.setdefault(b['host'] + ':' + b['outcome'], []).append((key, oid))
    controls = {g: min(values)[1] for g, values in groups.items()}
    checks['control_selection_exact'] = controls == frozen['controls']['chosen']
    checks['control_candidate_counts'] = {g: len(v) for g, v in groups.items()} == frozen['controls']['candidates']
    platform = []
    for ref in original['runs']:
        if ref['block'] == 'perturb':
            continue
        for r in jsonl(ROOT / ref['run'] / 'jobs.jsonl'):
            old = b144.get(r['orbit_id']) if r['code'] == 'B' else dt.get((r['orbit_id'], r['code']))
            if old is None:
                continue
            platform.append(dict(orbit_id=r['orbit_id'], code=r['code'], block=ref['block'],
                same_label=r['outcome'] == old['outcome'], same_time=time_of(r) == time_of(old),
                same_steps=r['steps'] == old['steps'], same_final_probe=r['final_state'][-6:] == old['final_probe']))
    checks['P0_archive_bitwise_9'] = sum(p['block'] == 'P0' for p in platform) == 9 and all(
        all(p[k] for k in ('same_label', 'same_time', 'same_steps', 'same_final_probe'))
        for p in platform if p['block'] == 'P0')
    candidate = next(c for c in audit['cases'] if c['orbit_id'] == 'himalia:uniform_a_e_cos_i_phases:0012')
    delta = candidate['dt_from_U_p0']
    checks['candidate_time_contraction'] = abs(delta['Ts']) <= 1 and abs(delta['Ts']) <= 0.1 * abs(delta['T'] - delta['Ts'])
    checks['candidate_D_time_differs_T_within_budget'] = abs(delta['D']) > 1 and abs(delta['T']) <= 1
    checks['candidate_B144_not_contradictory'] = candidate['b144']['outcome'] == candidate['labels']['U'] and abs(candidate['b144']['dt_from_U_p0']) <= 1
    checks['candidate_perturbations_complete_stable'] = all(p['n'] == p['unique_directions'] == 10 and
        p['unresolved_or_incomplete'] == p['physical_flips'] == 0 and p['max_abs_dt_p0'] <= 1
        for p in candidate['perturbation'].values())
    b_flags = [c['orbit_id'] for c in audit['cases'] if c['high_e'] and
               len({c['labels'][code] for code in ('B', 'Bs', 'Be')}) > 1]
    checks['rule5_b_labels_differ'] = set(b_flags) == {'iapetus:uniform_a_e_cos_i_phases:0011', 'himalia:canonical_volume:0013'}
    checks['controls_no_label_changes'] = all(len(set(c['labels'].values())) == 1 for c in audit['cases'] if c['role'] == 'control')
    result = dict(checks=checks, passed=all(checks.values()), source_sha256={p: sha(p) for p in
        (args.audit, args.replay, str(ROOT / ANALYSIS), str(Path(__file__)))},
        replay_differences=len(diff), replay_max_relative_difference=max((abs(d['old'] - d['new']) /
            max(abs(d['old']), abs(d['new'])) for d in diff), default=0),
        replay_difference_fields=dict(Counter('/'.join(d['path'].split('/')[-2:]) for d in diff)),
        candidate=candidate, rule5_units=b_flags, controls=controls, archive_platform=platform,
        candidate_D_minus_U_days=delta['D'] * candidate['p0_seconds'] / (YEAR / 365.25),
        limitations='Кандидат T поддержан для времени одного контакта; не эталон, не допуск W1/W3. Классификатор v1 не исправлен.')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
