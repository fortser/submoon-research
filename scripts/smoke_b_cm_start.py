"""W2-T043: short regression of B with drift-free start against archived P2-2a B rows (100 years).

Six fast B jobs of run W2-P2-2a-56220810 (Himalia/Ganymede; Iapetus excluded because its legacy
BLAS start differs on AVX2 CPUs, W2-I035). Units are regenerated with the legacy generator and
accepted only if the archived job fingerprint and the initial probe state match exactly.
Pre-registered pass rule: all identities match, all new B jobs completed, and outcome, event type
and body equal the archived B rows. Event-time and trajectory differences are descriptive
(flag if |dt_event| > 1e-6 P0). Not a scientific admission of B.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402

import admission_p2_2 as stand  # noqa: E402
from submoon_research.contracts.p2_files import atomic_json, file_digest, read_json  # noqa: E402
from submoon_research.dynamics import engine_compare  # noqa: E402
from submoon_research.dynamics.native_ias15 import NativeIAS15  # noqa: E402
from submoon_research.exact_linalg import LEGACY, numerics  # noqa: E402

RUN = ROOT/'runs/W2-P2-2a-56220810'
SUMMARY_SHA = '7308d42f368bb94753904c3f1085b0afb2039c9d9c86ad4ae26776e1518f135f'
JOBS_SHA = '0c17c36ac24b7bf0f8cd6ab939ef8464e3a51daf82265ab1f637b6a5fef41175'
ORBITS = ('ganymede:uniform_log_a:0002', 'himalia:canonical_volume:0010', 'ganymede:uniform_log_a:0000',
          'himalia:uniform_a_e_cos_i_phases:0015', 'himalia:canonical_volume:0012',
          'himalia:uniform_log_a:0014')
YEARS = 100.0
OUT = ROOT/'results/evidence/W2_B_cmstart_20261020/smoke.json'


class Recording(NativeIAS15):
    maxima = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Recording.maxima.append(0.)

    def step(self, *args, **kwargs):
        segment = super().step(*args, **kwargs)
        Recording.maxima[-1] = max(Recording.maxima[-1], float(np.max(np.abs(self.absolute_state()[:, :3]))))
        return segment


def compare(unit, old, new):
    period = unit['period_seconds']
    n = 2*math.pi/period
    a = unit['a_km']
    old_s = {float(t): np.asarray(s) for t, s in old['samples']}
    rows, tau = [], None
    for t, s in new['samples']:
        if float(t) in old_s:
            d = np.asarray(s)-old_s[float(t)]
            dr, dv = float(np.linalg.norm(d[:3]))/a, float(np.linalg.norm(d[3:]))/(n*a)
            rows.append((float(t), dr, dv))
            if tau is None and max(dr, dv) > 1e-6:
                tau = float(t)
    ev_old, ev_new = old['event'], new['event']
    dt_event = None if not (ev_old and ev_new) else abs(ev_new['time']-ev_old['time'])/period
    same = (new['run_status'] == 'completed' and new['outcome'] == old['outcome']
            and (ev_old is None) == (ev_new is None)
            and (ev_old is None or (ev_old['event'], ev_old['body']) == (ev_new['event'], ev_new['body'])))
    return dict(same_outcome_event=same, old_outcome=old['outcome'], new_outcome=new['outcome'],
                new_status=new['run_status'], new_error=new.get('error'), event_dt_over_P0=dt_event,
                event_flag=dt_event is not None and dt_event > 1e-6, common_samples=len(rows),
                max_dr_over_a=max((r[1] for r in rows), default=None),
                max_dv_over_na=max((r[2] for r in rows), default=None),
                tau_1e6_s=tau, old_wall=old['wall'], new_wall=new['wall'],
                old_steps=old['steps'], new_steps=new['steps'])


def main():
    if OUT.exists():
        raise SystemExit('smoke.json already exists; no reruns')
    if file_digest(RUN/'summary.json') != SUMMARY_SHA or file_digest(RUN/'jobs.jsonl') != JOBS_SHA:
        raise ValueError('Pinned P2-2a source changed')
    summary = read_json(RUN/'summary.json')
    server_impl = summary['implementations']
    rows = {}
    for line in (RUN/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        r = json.loads(line)
        rows[(r['orbit_id'], r['years'], r['variant'])] = r
    new_b = stand.b_fingerprint()
    engine_compare.NativeIAS15 = Recording
    units = {}
    with numerics(summary.get('generator_numerics', LEGACY)):
        prepared = stand.setups()
        for orbit in ORBITS:
            j = int(orbit.rsplit(':', 1)[1])
            units[orbit] = next(u for u in stand.units(1, j, prepared) if u['orbit_id'] == orbit)
    results, started = {}, time.monotonic()
    for orbit in ORBITS:
        unit = units[orbit]
        old = rows[(orbit, YEARS, 'B')]
        identity = dict(job_fingerprint=stand.job_fingerprint(unit, YEARS, 'B', server_impl) == old['job_fingerprint'],
                        initial_probe_exact=np.array_equal(unit['state'][-6:], old['samples'][0][1]))
        if not all(identity.values()):
            results[orbit] = dict(identity=identity, skipped='identity mismatch; not integrated')
            continue
        fp = stand.job_fingerprint(unit, YEARS, 'B', dict(server_impl, B=new_b))
        Recording.maxima.clear()
        new = stand._job(dict(unit=unit, years=YEARS, variant='B', fingerprint=fp))
        state = np.asarray(unit['state']).reshape(-1, 6)
        w = np.r_[unit['gms'], 0.]
        v_cm = float(np.linalg.norm((w[:, None]*state[:, 3:]).sum(axis=0)/w.sum()))
        results[orbit] = dict(identity=identity, **compare(unit, old, new),
                              max_abs_coordinate_km=max(Recording.maxima) if Recording.maxima else None,
                              old_drift_estimate_km=v_cm*min(new['last_valid_time'], YEARS*stand.YEAR),
                              cm_speed_km_s=v_cm)
        print(orbit, {k: results[orbit][k] for k in ('same_outcome_event', 'old_outcome', 'new_outcome',
                                                    'event_dt_over_P0', 'tau_1e6_s', 'max_dr_over_a',
                                                    'max_abs_coordinate_km', 'old_drift_estimate_km',
                                                    'new_wall')}, flush=True)
    passed = all(r.get('identity') and all(r['identity'].values()) and r.get('same_outcome_event')
                 for r in results.values())
    out = dict(task='W2-T043', run=str(RUN.relative_to(ROOT)), years=YEARS, orbits=ORBITS,
               B_implementation_old=server_impl['B'], B_implementation_new=new_b,
               rule=__doc__, results=results, passed=passed, wall_seconds=time.monotonic()-started,
               flags=[o for o, r in results.items() if r.get('event_flag')],
               scientific_admission=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(OUT, out)
    print('SMOKE passed =', passed, 'flags =', out['flags'], 'wall', round(out['wall_seconds'], 1), 's')
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
