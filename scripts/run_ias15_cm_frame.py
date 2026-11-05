"""W2-P014 step 1 driver for WSL: synthetic tests, prepare, G and GF once each, analyze.

Limits per variant: 1 CPU, 1 thread, RLIMIT_AS 2 GiB, file <= 60 MiB, 90 s process;
inside the variant <= 60 s / 10000 attempts; whole package <= 600 s. No reruns.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ('G', 'GF')
DIRECTORY = 'results/evidence/W2_B_cmframe_20261020'
TESTS = ['tests/unit/test_ias15_cm_frame.py', 'tests/unit/test_ias15_memory_controls.py',
         'tests/unit/test_ias15_frame_controls.py']
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS',
           'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')
TOTAL_LIMIT, PROCESS_LIMIT = 600., 90.


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def limits():
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    resource.setrlimit(resource.RLIMIT_AS, (2*1024**3, 2*1024**3))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (60*1024**2, 60*1024**2))


def env():
    e = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    e.update({k: '1' for k in THREADS})
    return e


def run(cmd, *, timeout, log, limited=False):
    started = time.monotonic()
    print('$', ' '.join(cmd), flush=True)
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=env(), timeout=timeout, capture_output=True, text=True,
                              preexec_fn=limits if limited else None)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = 'process_timeout'
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or '')
    wall = time.monotonic()-started
    print(out[-6000:], flush=True)
    if err.strip():
        print('--- stderr ---\n'+err[-6000:], flush=True)
    print(f'exit {code}  wall {wall:.2f} s', flush=True)
    return dict(command=cmd, returncode=code, wall_seconds=wall, finished_utc=now(),
                stdout_tail=out[-4000:], stderr_tail=err[-4000:])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--directory', default=DIRECTORY)
    args = p.parse_args(argv)
    py = sys.executable
    directory = ROOT/args.directory
    print('=== W2-P014 step 1 CM-frame controls', now(), 'python', py, flush=True)
    print('=== 1. synthetic tests (must pass before any real variant)', flush=True)
    tests = run([py, '-m', 'pytest', '-q', '-rs', *TESTS], timeout=300, log=None)
    if tests['returncode'] != 0:
        print('STOP: tests failed; real variants not started', flush=True)
        return 1
    print('=== 2. ruff (if installed in this venv)', flush=True)
    run([py, '-m', 'ruff', 'check', 'src/submoon_research/diagnostics/ias15_cm_frame.py',
         'scripts/diag_ias15_cm_frame.py', 'scripts/run_ias15_cm_frame.py',
         'tests/unit/test_ias15_cm_frame.py'], timeout=120, log=None)
    print('=== 3. prepare', flush=True)
    prep = run([py, 'scripts/diag_ias15_cm_frame.py', '--directory', args.directory, '--prepare'],
               timeout=120, log=None, limited=True)
    if prep['returncode'] != 0:
        print('STOP: prepare failed', flush=True)
        return 1
    sha = prep['stdout_tail'].strip().splitlines()[-1].strip()
    manifest = dict(run_id='W2-B-cmframe-20261020', started_utc=now(), inputs_sha256=sha, tests=tests,
                    prepare=prep, limits=dict(total_seconds=TOTAL_LIMIT, process_seconds=PROCESS_LIMIT,
                                              rlimit_as='2 GiB', rlimit_fsize='60 MiB', cpu=1))
    executions, total, stopped = [], 0., None
    print('=== 4. variants, one process each, no reruns', flush=True)
    for v in VARIANTS:
        if total+PROCESS_LIMIT > TOTAL_LIMIT:
            stopped = f'total budget: {total:.1f} s used before {v}'
            break
        r = run([py, 'scripts/diag_ias15_cm_frame.py', '--directory', args.directory, '--variant', v,
                 '--inputs-sha256', sha], timeout=PROCESS_LIMIT, log=None, limited=True)
        r.update(variant=v, output_exists=(directory/(v+'.json')).exists())
        executions.append(r)
        total += r['wall_seconds']
    print('=== 5. analyze', flush=True)
    analysis = run([py, 'scripts/diag_ias15_cm_frame.py', '--directory', args.directory, '--analyze',
                    '--inputs-sha256', sha], timeout=120, log=None, limited=True)
    manifest.update(executions=executions, total_variant_wall_seconds=total, stopped=stopped,
                    analysis=analysis, finished_utc=now(),
                    complete=stopped is None and len(executions) == len(VARIANTS))
    out = directory/'execution.json'
    if out.exists():
        raise ValueError('execution.json already exists')
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('=== done; stopped =', stopped, '; total variant wall', round(total, 2), 's', flush=True)
    return 0 if manifest['complete'] and analysis['returncode'] == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())
