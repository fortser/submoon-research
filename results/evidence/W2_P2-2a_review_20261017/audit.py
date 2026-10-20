"""Read-only independent aggregation of the archived P2-2a rows; no integration."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[3]
RUN = ROOT / 'runs/W2-P2-2a-56220810'
YEAR = 365.25 * 86400


def label(row):
    outcome = row['outcome']
    return outcome + ':' + row['unresolved_reason'] if outcome == 'unresolved' else outcome


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    rows = [json.loads(line) for line in (RUN / 'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    keys = [(r['orbit_id'], r['years'], r['variant']) for r in rows]
    final = {(r['orbit_id'], r['variant']): r for r in rows if r['years'] == 1000}
    ids = sorted(orbit for orbit, variant in final if variant == 'D')
    mismatches = []
    by_cell = {}
    for orbit in ids:
        d, t = final[orbit, 'D'], final[orbit, 'T']
        cell = orbit.rsplit(':', 1)[0]
        count = by_cell.setdefault(cell, Counter())
        count['n'] += 1
        count['discordant'] += label(d) != label(t)
        for v, row in [('D', d), ('T', t)]:
            count[v + ':' + label(row)] += 1
        if label(d) != label(t):
            mismatches.append(dict(orbit_id=orbit, D=label(d), T=label(t),
                                   D_time_years=d['last_valid_time'] / YEAR,
                                   T_time_years=t['last_valid_time'] / YEAR))
    b_pairs = {}
    for variant in ['D', 'T']:
        pairs = [(final[orbit, variant], b) for (orbit, v), b in final.items() if v == 'B']
        no_high = [(a, b) for a, b in pairs if not str(a.get('unresolved_reason')).startswith('high_e')]
        completed = [(a, b) for a, b in no_high if a['run_status'] == b['run_status'] == 'completed']
        b_pairs[variant] = dict(
            all_n=len(pairs), all_mismatches=sum(label(a) != label(b) for a, b in pairs),
            excluding_high_e_n=len(no_high),
            excluding_high_e_mismatches=sum(label(a) != label(b) for a, b in no_high),
            completed_excluding_high_e_n=len(completed),
            completed_excluding_high_e_mismatches=sum(label(a) != label(b) for a, b in completed),
            completed_discordant_ids=[a['orbit_id'] for a, b in completed if label(a) != label(b)])
    costs = {}
    for variant in ['D', 'T', 'B']:
        sub = [r for r in rows if r['variant'] == variant]
        costs[variant] = dict(jobs=len(sub), cpu_hours=sum(r['cpu'] for r in sub) / 3600,
                              worker_wall_hours=sum(r['wall'] for r in sub) / 3600,
                              failed_wall_hours=sum(r['wall'] for r in sub if r['run_status'] == 'failed') / 3600,
                              longest_wall_hours=max(r['wall'] for r in sub) / 3600)
    n, x = len(ids), len(mismatches)
    output = dict(run_id=RUN.name, rows=len(rows), unique_keys=len(set(keys)),
                  statuses=dict(Counter(r['run_status'] for r in rows)),
                  n=n, discordant=x, cp_upper_95=float(beta.ppf(.95, x + 1, n - x)),
                  cp_interval_95=[float(beta.ppf(.025, x, n - x + 1)),
                                  float(beta.ppf(.975, x + 1, n - x))],
                  final_outcomes={v: dict(Counter(label(r) for (_, w), r in final.items() if w == v))
                                  for v in ['D', 'T', 'B']},
                  cells=by_cell, mismatches=mismatches, b_pairs=b_pairs, cost=costs,
                  failed=[{k: r[k] for k in ['orbit_id', 'variant', 'years', 'error', 'last_valid_time', 'wall', 'cpu']}
                          for r in rows if r['run_status'] == 'failed'],
                  sha256={p.relative_to(ROOT).as_posix(): digest(p) for p in [
                      RUN / 'jobs.jsonl', RUN / 'analysis.json', RUN / 'summary.json',
                      ROOT / 'scratch/p2_2a_56220810/bundle.tar.gz',
                      ROOT / 'scratch/p2_2a_56220810/results_56220810.tar.gz']})
    path = Path(__file__).with_name('audit.json')
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
