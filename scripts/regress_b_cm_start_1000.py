"""W2-P014 step 3: himalia:uniform_log_a:0008 from t=0 to 1000 years with the drift-free B start.

The archived B job of run W2-P2-2a-56220810 failed at ~808.9 years (W2-I034). The unit is
regenerated with the legacy generator and accepted only if the archived job fingerprint and
initial probe state match exactly. One attempt, 1 CPU, deadline 7200 s (then 'partial', not a
numerical outcome). Pre-registered pass rule: identity matches AND the new B job is 'completed'
at 1000 years (no guard/failure). Descriptive: outcome and trajectory vs archived D/T (both
survived), small-step windows (dt < 10 s / < 1 s), minimum dt, maximum |x|, steps and wall.
Not a scientific admission of B.
"""
from __future__ import annotations

import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402

import admission_p2_2 as stand  # noqa: E402
from smoke_b_cm_start import JOBS_SHA, RUN, SUMMARY_SHA, compare  # noqa: E402
from submoon_research.contracts.p2_files import atomic_json, file_digest, loads, read_json  # noqa: E402
from submoon_research.dynamics import engine_compare  # noqa: E402
from submoon_research.dynamics.native_ias15 import NativeIAS15  # noqa: E402
from submoon_research.exact_linalg import LEGACY, numerics  # noqa: E402

ORBIT, YEARS, DEADLINE_S = 'himalia:uniform_log_a:0008', 1000.0, 7200.
OUT = ROOT/'results/evidence/W2_B_regress_20261020/regress.json'


class Monitor(NativeIAS15):
    """Cheap per-step bookkeeping; does not change the integration."""
    stats = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Monitor.stats = dict(steps=0, min_dt=float('inf'), min_dt_time=None, below_10s=0, below_1s=0,
                             windows=[], max_abs_coordinate_km=0., started=time.monotonic())
        self._window = None

    def step(self, *args, **kwargs):
        segment = super().step(*args, **kwargs)
        s, dt, t = Monitor.stats, float(self.sim.dt_last_done), float(self.sim.t)
        s['steps'] += 1
        if dt < s['min_dt']:
            s['min_dt'], s['min_dt_time'] = dt, t
        if dt < 10.:
            s['below_10s'] += 1
            s['below_1s'] += dt < 1.
            if self._window is None:
                self._window = dict(start_s=t-dt, min_dt=dt, steps=0)
            self._window['min_dt'] = min(self._window['min_dt'], dt)
            self._window['steps'] += 1
            self._window['end_s'] = t
            s['open_window'] = self._window
        elif self._window is not None:
            s['open_window'] = None
            if len(s['windows']) < 2000:
                s['windows'].append(self._window)
            self._window = None
        if s['steps'] % 20000 == 0:
            s['max_abs_coordinate_km'] = max(s['max_abs_coordinate_km'],
                                             float(np.max(np.abs(self.absolute_state()[:, :3]))))
        if s['steps'] % 500000 == 0:
            print(f'  progress: {t/stand.YEAR:8.2f} yr, steps {s["steps"]}, '
                  f'wall {time.monotonic()-s["started"]:.0f} s, min dt {s["min_dt"]:.3g} s', flush=True)
        return segment


def constrain():
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))


def main():
    if OUT.exists():
        raise SystemExit('regress.json already exists; no reruns')
    constrain()
    if file_digest(RUN/'summary.json') != SUMMARY_SHA or file_digest(RUN/'jobs.jsonl') != JOBS_SHA:
        raise ValueError('Pinned P2-2a source changed')
    summary = read_json(RUN/'summary.json')
    server_impl = summary['implementations']
    rows = {}
    for line in (RUN/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        r = loads(line)
        if r['orbit_id'] == ORBIT:
            rows[(r['years'], r['variant'])] = r
    with numerics(summary.get('generator_numerics', LEGACY)):
        prepared = stand.setups()
        unit = next(u for u in stand.units(1, int(ORBIT.rsplit(':', 1)[1]), prepared) if u['orbit_id'] == ORBIT)
    old = rows[(YEARS, 'B')]
    identity = dict(job_fingerprint=stand.job_fingerprint(unit, YEARS, 'B', server_impl) == old['job_fingerprint'],
                    initial_probe_exact=np.array_equal(unit['state'][-6:], old['samples'][0][1]))
    print('identity', identity, flush=True)
    if not all(identity.values()):
        raise SystemExit('identity mismatch; not integrated')
    new_b = stand.b_fingerprint()
    fp = stand.job_fingerprint(unit, YEARS, 'B', dict(server_impl, B=new_b))
    engine_compare.NativeIAS15 = Monitor
    started = time.monotonic()
    print('start', ORBIT, YEARS, 'years; deadline', DEADLINE_S, 's', flush=True)
    new = stand._job(dict(unit=unit, years=YEARS, variant='B', fingerprint=fp,
                          deadline=time.time()+DEADLINE_S))
    wall = time.monotonic()-started
    stats = dict(Monitor.stats or {})
    stats.pop('started', None)
    versus = {k: compare(unit, rows[(YEARS, k)], new) for k in ('D', 'T')}
    versus['B_old'] = compare(unit, old, new)
    passed = all(identity.values()) and new['run_status'] == 'completed'
    out = dict(task='W2-T044', orbit=ORBIT, years=YEARS, rule=__doc__, identity=identity, passed=passed,
               new_status=new['run_status'], new_outcome=new['outcome'], new_error=new['error'],
               last_valid_years=new['last_valid_time']/stand.YEAR, new_steps=new['steps'], wall_seconds=wall,
               old_B=dict(status=old['run_status'], last_valid_years=old['last_valid_time']/stand.YEAR,
                          steps=old['steps'], wall_server=old['wall'], error=old.get('error')),
               versus=versus, step_stats=stats, B_implementation_old=server_impl['B'],
               B_implementation_new=new_b, scientific_admission=False)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(OUT, out)
    print('RESULT passed =', passed, '| status', new['run_status'], '| outcome', new['outcome'],
          '| years', round(out['last_valid_years'], 3), '| steps', new['steps'], '| wall', round(wall), 's')
    print('  min dt', stats.get('min_dt'), 'at', stats.get('min_dt_time'), '| steps dt<10 s', stats.get('below_10s'),
          '| dt<1 s', stats.get('below_1s'), '| windows', len(stats.get('windows', [])),
          '| max |x|', stats.get('max_abs_coordinate_km'))
    for k, v in versus.items():
        print('  vs', k, {x: v[x] for x in ('same_outcome_event', 'old_outcome', 'tau_1e6_s', 'max_dr_over_a')})
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
