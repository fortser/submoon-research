"""OPT-08: ограниченный повтор B с fail-closed идентичностью и pre-step snapshot.

По умолчанию максимум 60 s / 10000 шагов, общий предел 600 s.
Исключение W2-D037 — один фиксированный локальный профиль со своим launcher. Диагностика не меняет NativeIAS15,
силы, шаг, guard или событие. Старый отчёт и исходный run не перезаписываются.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import admission_p2_2 as stand  # noqa: E402
from submoon_research.contracts.frozen_p2 import load_frozen_inputs  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, file_digest, loads, output_path, read_job_rows, read_json,
)
from submoon_research.diagnostics.ias15_replay import StepTrace, environment  # noqa: E402
from submoon_research.exact_linalg import LEGACY, numerics  # noqa: E402


def prepare_identity(args):
    """Ни интеграций, ни записей до полного совпадения доверенного B-контракта."""
    run = ROOT/args.run
    if (file_digest(run/'summary.json') != args.source_summary_sha256
            or file_digest(run/'jobs.jsonl') != args.source_jobs_sha256):
        raise ValueError('SHA источника не совпал с внешним якорем')
    summary = read_json(run/'summary.json')
    raw = [loads(line) for line in (run/'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
    fp = {(r['orbit_id'], r['years'], r['variant']): r['job_fingerprint'] for r in raw}
    rows = read_job_rows(run/'jobs.jsonl', fp)
    server = rows.get((args.orbit, args.years, 'B'))
    if server is None:
        raise ValueError('Нет B-задания в закреплённом источнике')
    if bool(args.inputs_manifest) != bool(args.inputs_manifest_sha256):
        raise ValueError('Нужны вместе inputs manifest и внешний SHA')
    if args.inputs_manifest:
        doc, _ = load_frozen_inputs(ROOT/args.inputs_manifest, args.inputs_manifest_sha256)
        unit = next((u for u in doc['units'] if u['orbit_id'] == args.orbit), None)
        basis = 'frozen inputs'
    else:
        j = int(args.orbit.rsplit(':', 1)[1])
        with numerics(summary.get('generator_numerics', LEGACY)):
            unit = next((u for u in stand.units(1, j) if u['orbit_id'] == args.orbit), None)
        basis = 'legacy generator; checked against trusted job fingerprint'
    if unit is None:
        raise ValueError('Нет выбранной единицы входов')
    server_impl = summary['implementations']
    current_b = stand.b_fingerprint()
    expected_fp = stand.job_fingerprint(unit, args.years, 'B', server_impl)
    checks = dict(job_fingerprint_matches=expected_fp == server['job_fingerprint'],
                  implementation_B_matches=current_b == server_impl['B'],
                  initial_probe_exact=np.array_equal(unit['state'][-6:], server['samples'][0][1]))
    if not all(checks.values()):
        raise ValueError('Отказ до интеграции: не совпала идентичность '+str(checks))
    identity = dict(orbit=args.orbit, years=args.years, source_run=args.run, job_fingerprint=expected_fp,
                    B_implementation=current_b, source_summary_sha256=args.source_summary_sha256,
                    source_jobs_sha256=args.source_jobs_sha256,
                    inputs_manifest_sha256=args.inputs_manifest_sha256,
                    source_inputs_manifest=args.inputs_manifest,
                    period_seconds=unit['period_seconds'], gm_host=unit['gms'][0],
                    step_fraction=stand.NumericMode().step_fraction)
    return unit, server, identity, dict(checks=checks, input_basis=basis)


def parse(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--run', required=True)
    p.add_argument('--source-summary-sha256', required=True)
    p.add_argument('--source-jobs-sha256', required=True)
    p.add_argument('--orbit', required=True)
    p.add_argument('--years', type=float, required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--inputs-manifest')
    p.add_argument('--inputs-manifest-sha256')
    p.add_argument('--preflight-only', action='store_true')
    p.add_argument('--ring', type=int, default=128)
    p.add_argument('--trace-after', type=float, default=0.0)
    p.add_argument('--snapshot-at', type=float)
    p.add_argument('--small-dt', type=float, default=1e-3)
    p.add_argument('--max-steps', type=int, default=10000)
    p.add_argument('--wall-seconds', type=float, default=60.0)
    return p.parse_args(argv)


CAPTURE_OUTPUT = 'results/evidence/W2_B_capture_20261019/attempt/diagnostic.json'


def validate_budget(args, capture_approval=None):
    if capture_approval is None:
        if not 0 < args.wall_seconds <= 600 or not 1 <= args.max_steps <= 10_000_000:
            raise ValueError('Часовой проход не разрешён: wall<=600 s, steps<=10000000')
        return 600
    expected = dict(run='runs/W2-P2-2a-56220810', orbit='himalia:uniform_log_a:0008',
                    years=1000., output=CAPTURE_OUTPUT, wall_seconds=5370.,
                    max_steps=10_000_000, ring=512, snapshot_at=25525800000.,
                    trace_after=25525800000., small_dt=0., inputs_manifest=None,
                    inputs_manifest_sha256=None,
                    source_summary_sha256='7308d42f368bb94753904c3f1085b0afb2039c9d9c86ad4ae26776e1518f135f',
                    source_jobs_sha256='0c17c36ac24b7bf0f8cd6ab939ef8464e3a51daf82265ab1f637b6a5fef41175')
    if capture_approval != 'W2-D037' or any(getattr(args, k) != v for k, v in expected.items()):
        raise ValueError('Параметры не соответствуют одноразовому профилю W2-D037')
    return 5400


def main(argv=None, *, capture_approval=None):
    args = parse(argv)
    wall_limit = validate_budget(args, capture_approval)
    path = output_path(ROOT/args.output, ROOT/args.run)
    if args.inputs_manifest:
        output_path(path, (ROOT/args.inputs_manifest).resolve().parent)
    unit, server, identity, preflight = prepare_identity(args)
    if capture_approval and not args.preflight_only:
        # Exclusive-маркер не удаляется даже при ошибке: повтор требует нового согласия.
        with (path.parent/'worker_started.json').open('x', encoding='utf-8') as handle:
            json.dump(dict(approval=capture_approval, identity=identity, utc_epoch=time.time()), handle)
    report = dict(task='W2-T035' if capture_approval else 'W2-T034',
                  capture_approval=capture_approval,
                  identity=identity, preflight=preflight, environment=environment(),
                  diagnostic_source_sha256=file_digest(Path(__file__)), integration_status='not_run',
                  scientific_admission=False)
    if not args.preflight_only:
        import submoon_research.dynamics.native_ias15 as native
        trace = StepTrace(directory=path.parent/(path.stem+'_artifacts'), figures=unit['figures'],
                          identity=identity, ring=args.ring, trace_after=args.trace_after,
                          snapshot_at=args.snapshot_at, small_dt=args.small_dt,
                          max_steps=args.max_steps, wall_seconds=args.wall_seconds,
                          wall_limit_seconds=wall_limit)
        original = native.NativeIAS15.step
        native.NativeIAS15.step = lambda self, bound, max_step=np.inf: trace.step(original, self, bound, max_step)
        try:
            result = stand._job(dict(unit=unit, years=args.years, variant='B',
                fingerprint=identity['job_fingerprint'], deadline=time.time()+args.wall_seconds, stop_file=None))
        finally:
            native.NativeIAS15.step = original
        report.update(integration_status=result['run_status'], local=result, trace=trace.report(),
                      same_failure=(result['run_status'] == server['run_status']
                                    and result.get('error') == server.get('error')
                                    and result.get('last_valid_time') == server.get('last_valid_time')))
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, report)
    print(json.dumps(dict(report=str(path), integration_status=report['integration_status'],
                          preflight=preflight, scientific_admission=False), ensure_ascii=False))
    return 0 if args.preflight_only or report['integration_status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
