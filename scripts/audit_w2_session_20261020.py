"""W2-T047: независимый пересчёт артефактов и ограниченный повтор snapshot.

Не запускает длинные орбиты. Новый каталог обязателен; исходные данные не меняются.
--replay: семь коротких вариантов, <=60 s/10000 шагов каждый; запуск в WSL ABI снимка.
Основная арифметика a/j/snap и статистики не использует прежние анализаторы.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]
from submoon_research.diagnostics.w2_session_audit import derivatives, j2_probe  # noqa: E402

SOURCES = {}


def read(path):
    path = ROOT/path
    raw = path.read_bytes()
    SOURCES[path.relative_to(ROOT).as_posix()] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw)


def write(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--replay', action='store_true')
    args = parser.parse_args()
    out = ROOT/args.output
    out.mkdir(parents=True, exist_ok=False)
    if hasattr(os, 'sched_setaffinity'):
        getattr(os, 'sched_setaffinity')(0, {min(getattr(os, 'sched_getaffinity')(0))})
    jobs_path = ROOT/'runs/W2-P2-2a-56220810/jobs.jsonl'
    SOURCES[jobs_path.relative_to(ROOT).as_posix()] = hashlib.sha256(jobs_path.read_bytes()).hexdigest()
    jobs = [json.loads(line) for line in jobs_path.read_text(encoding='utf-8').splitlines()]
    indexed = {(r['orbit_id'], r['years'], r['variant']): r for r in jobs}
    pairs = []
    for (orbit, years, variant), d in indexed.items():
        if years != 1000 or variant != 'D':
            continue
        t = indexed[orbit, years, 'T']
        if (d['outcome'], d.get('unresolved_reason')) != (t['outcome'], t.get('unresolved_reason')):
            pairs.append(dict(orbit=orbit, D=[d['outcome'], d.get('unresolved_reason')],
                              T=[t['outcome'], t.get('unresolved_reason')]))
    n = sum(r['years'] == 1000 and r['variant'] == 'D' for r in jobs)
    high_e = {v: sum(r['years'] == 1000 and r['variant'] == v and
                    r.get('unresolved_reason') == 'high_e_prolonged' for r in jobs) for v in ('D', 'T')}
    b_rows = [r for r in jobs if r['variant'] == 'B']
    stats = dict(rows=len(jobs), completed=sum(r['run_status'] == 'completed' for r in jobs),
                 n=n, disagreements=pairs, high_e=high_e,
                 cp_upper_iid_assumption=float(beta.ppf(.95, len(pairs)+1, n-len(pairs))),
                 B_worker_hours=sum(r['wall'] for r in b_rows)/3600,
                 B_max_wall_hours=max(r['wall'] for r in b_rows)/3600,
                 zero_of_144_upper_iid=float(1-.05**(1/144)))
    memory = 'results/evidence/W2_B_memory_20261020/'
    frames = 'results/evidence/W2_B_cmframe_20261020/'
    snap = 'results/evidence/W2_B_capture_20261019/attempt/diagnostic_artifacts/pre_step_snapshot/'
    physical = read(snap+'physical.json')
    inputs = read(memory+'inputs.json')
    cm_inputs = read(frames+'inputs.json')
    runs = {v: read(memory+v+'.json') for v in ('M0', 'MP', 'MC', 'MD', 'MA')}
    runs.update({v: read(frames+v+'.json') for v in ('G', 'GF')})
    derived = {}
    factor = (physical['adaptation']['epsilon']*5040)**(1/7)
    for v, r in runs.items():
        estimates, norms = [], []
        for rec in r['records']:
            a, j, sn, terms = derivatives(rec['absolute_state'], physical['gms'])
            aa, jj, ss = map(np.linalg.norm, (a, j, sn))
            estimates.append(float(math.sqrt(2)*aa/jj*factor))
            norms.append(float(aa))
        k = int(np.argmin(norms))
        a, j, sn, terms = derivatives(r['records'][k]['absolute_state'], physical['gms'])
        aa, jj, ss = map(np.linalg.norm, (a, j, sn))
        j2 = j2_probe(r['records'][k]['absolute_state'], physical['gms'], physical['figures'])
        clipped = [rec['dt_done'] for rec in r['records'] if rec['right'] < r['target_time']]
        # A rejected internal trial reduces dt before acceptance; no such reductions
        # in these M0 records provides evidence separate from MD == M0.
        reduced = [i for i, rec in enumerate(r['records']) if rec['dt_done'] <
                   min(rec['proposed_dt'], rec['max_step'], r['target_time']-rec['left'])]
        derived[v] = dict(status=r['status'], attempts=len(r['records']),
                          min_dt_unclipped=min(clipped), steps_below_1=sum(x['dt_done'] < 1 for x in r['records']),
                          min_acc_point_mass=float(aa), host_pull=float(np.linalg.norm(terms[0])),
                          external_pull=float(np.linalg.norm(terms[1:].sum(axis=0))),
                          j2_abs=float(np.linalg.norm(j2)), j2_over_total=float(np.linalg.norm(j2)/aa),
                          prs23_upper_min=min(estimates),
                          prs23_with_snap_at_min_a=float(np.sqrt(2*aa*aa/(jj*jj+aa*ss))*factor),
                          prs23_without_snap_at_min_a=estimates[k], internally_reduced_steps=reduced)
    # IEEE-754 binary data equality on the saved observables (not unseen solver memory).
    def same_records(a, b, keys):
        return len(a) == len(b) and all(all(x[k] == y[k] for k in keys) for x, y in zip(a, b))
    replay_ref = read('results/evidence/W2_B_replay_20261019/native_replay.json')
    p00 = read('results/evidence/W2_B_frames_20261019/matrix/P00.json')
    exact = dict(M0_C073=same_records(runs['M0']['records'][:-1], replay_ref['records'],
                                    ['left', 'right', 'dt_done', 'next_dt', 'absolute_state', 'coefficients_sha256']),
                 MD_M0=same_records(runs['MD']['records'], runs['M0']['records'],
                                    ['left', 'right', 'dt_done', 'next_dt', 'absolute_state', 'coefficients_sha256']),
                 MA_P00=same_records(runs['MA']['records'], p00['records'],
                                     ['left', 'right', 'dt_done', 'next_dt', 'candidate_dt_per_body']))
    state = np.asarray(physical['absolute_state'], dtype=np.longdouble)
    w = np.r_[physical['gms'], 0.]
    cm = (w[:, None]*state).sum(axis=0)/sum(w)
    geometry = dict(cm_speed=float(np.linalg.norm(cm[3:])), cm_radius=float(np.linalg.norm(cm[:3])),
                    drift_estimate=float(np.linalg.norm(cm[3:])*physical['time']),
                    max_component=float(np.max(np.abs(state[:, :3]))),
                    ulp_max_component=float(np.spacing(float(np.max(np.abs(state[:, :3]))))))
    # Independent state comparisons, no use of compare_samples().
    distances = {}
    for left, right in [('G', 'GF'), ('G', 'P00'), ('G', 'P10'), ('GF', 'P00')]:
        other = runs[right] if right in runs else read('results/evidence/W2_B_frames_20261019/matrix/'+right+'.json')
        amap = {r['elapsed']: np.asarray(r['relative_state'])[-1, :3] for r in runs[left]['samples']}
        bmap = {r['elapsed']: np.asarray(r['relative_state'])[-1, :3] for r in other['samples']}
        common = set(amap) & set(bmap)
        distances[left+'-'+right] = dict(points=len(common), max_km=max(float(np.linalg.norm(amap[t]-bmap[t])) for t in common))
    result = dict(scope='W2-T047; независимый пересчёт сохранённых данных, не длинная интеграция',
                  statistics=stats, snapshot_geometry=geometry, variants=derived, exact=exact, distances=distances)
    if args.replay:
        from submoon_research.diagnostics.ias15_memory_controls import apply_mask, integrate_window
        from submoon_research.diagnostics.ias15_cm_frame import run_cm
        from submoon_research.diagnostics.ias15_replay import load_snapshot, environment
        result['environment'] = environment()
        repeated = {}
        for v in ('M0', 'MP', 'MC', 'MD', 'MA', 'G', 'GF'):
            if v in ('G', 'GF'):
                new = run_cm(cm_inputs['variants'][v], inputs['identity'], inputs['grid'])
            else:
                native, _ = load_snapshot(ROOT/snap/'snapshot_manifest.json', inputs['snapshot_sha256'],
                                          expected_identity=inputs['identity'])
                apply_mask(native, v, physical['figures'])
                new = integrate_window(native, inputs['identity'], inputs['grid'])
            write(out/(v+'.json'), new)
            repeated[v] = dict(status=new['status'], exact_saved_steps=same_records(new['records'], runs[v]['records'],
                               ['left', 'right', 'dt_done', 'next_dt', 'absolute_state', 'coefficients_sha256']))
        result['repeated'] = repeated
    result['source_sha256'] = SOURCES
    result['script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    write(out/'audit.json', result)
    print(json.dumps({k: v for k, v in result.items() if k not in ('source_sha256',)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
