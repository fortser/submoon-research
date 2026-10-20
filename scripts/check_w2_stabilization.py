"""Короткие проверки стабилизации W2 с полным логом, JUnit и общим ненулевым кодом.

short: Windows-набор БЕЗ L1 CLI (исключения перечислены отдельно).
science: обязательные WSL-проверки стенда; любой skip означает неполноту.
l1: отдельное расследование L1 с исходным внутренним timeout=30 s.
Часовая диагностика IAS15 сюда намеренно не включена.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.contracts.p2_files import atomic_json  # noqa: E402

L1 = 'tests/integration/test_l1_acceptance.py'


def pytest_results(path):
    tests = []
    for case in ET.parse(path).getroot().iter('testcase'):
        state = next((tag for tag in ('failure', 'error', 'skipped') if case.find(tag) is not None), 'passed')
        tests.append(dict(name=case.get('classname', '')+'::'+case.get('name', ''), status=state,
                          seconds=float(case.get('time', 0)),
                          detail=None if state == 'passed' else case.find(state).get('message')))
    return dict(counts={s: sum(t['status'] == s for t in tests)
                        for s in ('passed', 'failure', 'error', 'skipped')}, tests=tests)


def run_step(name, command, out, *, timeout=600, junit=None, require_no_skips=False):
    started = time.perf_counter()
    log = out/(name+'.log')
    record = dict(name=name, command=command, log=log.name, status='failed', exit_code=None)
    env = dict(os.environ, PYTHONUTF8='1', OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1',
               MKL_NUM_THREADS='1', NUMBA_NUM_THREADS='1')
    with log.open('x', encoding='utf-8') as stream:
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=timeout)
            record['exit_code'] = result.returncode
            record['status'] = 'passed' if result.returncode == 0 else 'failed'
        except (OSError, subprocess.TimeoutExpired):
            traceback.print_exc(file=stream)
        if junit is not None:
            try:
                record['pytest'] = pytest_results(junit)
                counts = record['pytest']['counts']
                if counts['failure'] or counts['error'] or not record['pytest']['tests']:
                    record['status'] = 'failed'
                elif counts['skipped'] and record['status'] == 'passed':
                    record['status'] = 'incomplete' if require_no_skips else 'passed_with_skips'
            except (OSError, ValueError, ET.ParseError):
                traceback.print_exc(file=stream)
                record['status'] = 'failed'
    record['wall_seconds'] = time.perf_counter()-started
    return record


def finish(out, records, **metadata):
    ok = all(r['status'] in ('passed', 'passed_with_skips') for r in records)
    status = ('passed_with_skips' if any(r['status'] == 'passed_with_skips' for r in records)
              else 'passed') if ok else 'failed_or_incomplete'
    atomic_json(out/'checks.json', dict(utc=datetime.now(timezone.utc).isoformat(),
                                      status=status, steps=records, **metadata))
    print(f'ИТОГ ЗАЯВЛЕННОГО НАБОРА: {status}; {out}', flush=True)
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--suite', choices=['short', 'science', 'l1'], default='short')
    parser.add_argument('--output-dir')
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out = ROOT/(args.output_dir or f'results/evidence/W2_checks_{stamp}_{uuid.uuid4().hex[:8]}')
    out.mkdir(parents=True, exist_ok=False)
    py = sys.executable
    excluded = []
    records = []
    if args.suite == 'short':
        records.append(run_step('excluded_collection', [py, '-m', 'pytest', '--collect-only', '-q', L1], out))
        excluded = [s for s in (out/'excluded_collection.log').read_text(encoding='utf-8').splitlines()
                    if s.startswith('tests/') and '::' in s]
        targets = ['--ignore='+L1]
    elif args.suite == 'science':
        targets = ['tests/unit/test_p2_file_contracts.py', 'tests/unit/test_verify_p2_2_run.py',
                   'tests/unit/test_admission_evaluators.py', 'tests/unit/test_p2_2_stand.py',
                   'tests/unit/test_frozen_p2_inputs.py', 'tests/unit/test_ias15_replay.py',
                   'tests/unit/test_native_ias15_guard.py', 'tests/unit/test_ias15_dense_step.py',
                   'tests/unit/test_ias15_horizon_clip.py']
    else:
        targets = [L1]
    junit = out/'pytest.xml'
    records.append(run_step('pytest', [py, '-m', 'pytest', '-vv', '-ra', '--tb=long', '--durations=20',
                                      '--junitxml='+str(junit), *targets], out, junit=junit,
                            require_no_skips=args.suite != 'short'))
    if args.suite == 'short':
        for name, command in [('ruff', [py, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
                              ('validate', [py, 'scripts/project.py', 'validate']),
                              ('smoke', [py, 'scripts/project.py', 'smoke'])]:
            records.append(run_step(name, command, out))
    return finish(out, records, suite=args.suite, excluded_tests=excluded,
                  not_run=['IAS15 long diagnostic (requires separate explicit command)'],
                  scientific_admission=False)


if __name__ == '__main__':
    raise SystemExit(main())
