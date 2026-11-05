"""W2-T047: короткая независимая проверка старта B и повтор шести 100-летних заданий.

Один поток; <=600 s снаружи. Сохраняет полные новые строки, в отличие от старого smoke.
Не повторяет 1000-летний расчёт. Не меняет рабочие исходники или исторические артефакты.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'scripts')]

import numpy as np  # noqa: E402

import admission_p2_2 as stand  # noqa: E402
import smoke_b_cm_start as smoke  # noqa: E402
from submoon_research.dynamics.engine_compare import ias15_start_state, integrate_engine  # noqa: E402
from submoon_research.dynamics.native_ias15 import endpoint_residual  # noqa: E402
from submoon_research.exact_linalg import LEGACY, numerics  # noqa: E402

OUT = ROOT/'results/evidence/W2_full_audit_20261020/cm_start'


def save(name, data):
    with (OUT/name).open('x', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2, allow_nan=False)


def main():
    OUT.mkdir(exist_ok=False)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
    summary = json.loads((smoke.RUN/'summary.json').read_text())
    indexed = {}
    for line in (smoke.RUN/'jobs.jsonl').read_text().splitlines():
        row = json.loads(line)
        indexed[row['orbit_id'], row['years'], row['variant']] = row
    with numerics(LEGACY):
        units = stand.units(16, prepared=stand.setups())
    save('legacy_units.json', units)
    identities, rounding = [], []
    for u in units:
        old = indexed[u['orbit_id'], 1000., 'B']
        identities.append(dict(orbit=u['orbit_id'], matches=stand.job_fingerprint(
            u, 1000., 'B', summary['implementations']) == old['job_fingerprint']))
        before = np.asarray(u['state']).reshape(-1, 6)
        after = ias15_start_state(before, u['gms']).reshape(-1, 6)
        dv = (after[-1, 3:]-after[0, 3:])-(before[-1, 3:]-before[0, 3:])
        rounding.append(dict(orbit=u['orbit_id'], position_exact=bool(np.array_equal(before[:, :3], after[:, :3])),
                             relative_probe_velocity_error=float(np.linalg.norm(dv)/np.linalg.norm(before[-1, 3:])),
                             velocity_error_km_s=float(np.linalg.norm(dv))))
    # Контракт engine_compare не включает версию кода: старый физический checkpoint
    # принимается новым B. Это не повтор исторической траектории и не разрешение смешения.
    archived = ROOT/'scratch/claude/engine_compare.before_W2-T043.py'
    assert hashlib.sha256(archived.read_bytes()).hexdigest().startswith('dabbd6e1')
    spec = importlib.util.spec_from_file_location('w2_old_engine_audit', archived)
    old_engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old_engine
    spec.loader.exec_module(old_engine)
    u = next(u for u in units if u['orbit_id'] == 'himalia:uniform_log_a:0008')
    def stop():
        raise TimeoutError('audit: checkpoint at t=0, without integration')
    horizon = u['period_seconds']*.1
    old = old_engine.integrate_engine('ias15_reboundx', u['state'], u['gms'], u['figures'], u['radii'],
                                      horizon, period=u['period_seconds'], budget=stop)
    new = integrate_engine('ias15_reboundx', u['state'], u['gms'], u['figures'], u['radii'],
                           horizon, period=u['period_seconds'], resume=old['checkpoint'])
    checkpoint = dict(old_status=old['run_status'], new_status=new['run_status'],
                      contract_equal=old['checkpoint']['contract_sha256'] == new['checkpoint']['contract_sha256'],
                      scope='synthetic use of real initial state at t=0; old checkpoint accepted by new code')
    # Алгебраический контрпример: нормировка guard не инвариантна к переносу.
    end = np.zeros((2, 6)); end[1, 0] = 1.
    polynomial = end.copy(); polynomial[1, 0] += 1e-7
    translated, translated_p = end.copy(), polynomial.copy()
    translated[:, :3] += 1e6; translated_p[:, :3] += 1e6
    guard = dict(before=endpoint_residual(polynomial, end),
                 translated=endpoint_residual(translated_p, translated), threshold=1e-9,
                 scope='synthetic residual, not a real failed integration')
    save('checks.json', dict(identities=identities, rounding=rounding, checkpoint=checkpoint, guard=guard,
                             max_relative_probe_velocity_error=max(r['relative_probe_velocity_error'] for r in rounding)))
    # Повтор неизменных 6 заданий с сохранением полных входов/выходов.
    original_job = stand._job
    def recording_job(job):
        result = original_job(job)
        save(job['unit']['orbit_id'].replace(':', '_')+'.json', dict(job=job, result=result))
        return result
    stand._job = recording_job
    smoke.OUT = OUT/'smoke.json'
    code = smoke.main()
    print('checkpoint', checkpoint, 'guard', guard, flush=True)
    print('legacy matches', sum(r['matches'] for r in identities), '/', len(identities),
          'rounding max', max(r['relative_probe_velocity_error'] for r in rounding), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
