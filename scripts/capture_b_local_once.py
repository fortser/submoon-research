"""W2-D037: один фиксированный локальный проход B, без аренды и повторов.

WSL Ubuntu-24.04: --preflight (без интеграции), затем --launch.
Общий диагностический CLI и configs/resources.yaml сохраняют предел 600 s.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'results/evidence/W2_B_capture_20261019'
ATTEMPT = BASE/'attempt'
RUN_ID = 'W2-B-capture-20261019'
APPROVAL = 'W2-D037'
MEMORY = 2*1024**3
OUTPUT = 256*1024**2
THREAD_ENV = {k: '1' for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                              'NUMBA_NUM_THREADS', 'BLIS_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS')}


def utc():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    os.replace(tmp, path)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def code_hashes():
    paths = [Path(__file__), ROOT/'scripts/diag_b_failure_p2_2.py', ROOT/'scripts/admission_p2_2.py',
             ROOT/'src/submoon_research/diagnostics/ias15_replay.py',
             ROOT/'docs/tasks/W2_B_capture_20261019.md']
    paths += list((ROOT/'src/submoon_research/dynamics').glob('*.py'))
    paths += list((ROOT/'src/submoon_research/contracts').glob('*.py'))
    paths += [ROOT/'src/submoon_research/exact_linalg.py', ROOT/'src/submoon_research/sampling/design.py']
    return {p.relative_to(ROOT).as_posix(): sha(p) for p in paths}


def arguments():
    return ['--run', 'runs/W2-P2-2a-56220810',
            '--source-summary-sha256', '7308d42f368bb94753904c3f1085b0afb2039c9d9c86ad4ae26776e1518f135f',
            '--source-jobs-sha256', '0c17c36ac24b7bf0f8cd6ab939ef8464e3a51daf82265ab1f637b6a5fef41175',
            '--orbit', 'himalia:uniform_log_a:0008', '--years', '1000',
            '--output', 'results/evidence/W2_B_capture_20261019/attempt/diagnostic.json',
            '--wall-seconds', '5370', '--max-steps', '10000000', '--ring', '512',
            '--snapshot-at', '25525800000', '--trace-after', '25525800000', '--small-dt', '0']


def constrain_worker():
    import resource
    os.environ.update(THREAD_ENV)
    allowed = os.sched_getaffinity(0)
    os.sched_setaffinity(0, {min(allowed)})
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (240*1024**2, 240*1024**2))
    os.nice(10)


def diagnostic():
    sys.path.insert(0, str(ROOT/'scripts'))
    import diag_b_failure_p2_2
    return diag_b_failure_p2_2


def preflight():
    if BASE.joinpath('preflight.json').exists():
        raise FileExistsError('Preflight уже существует; не перезаписывать')
    constrain_worker()
    diag = diagnostic()
    args = diag.parse(arguments())
    diag.validate_budget(args, APPROVAL)
    _, _, identity, checks = diag.prepare_identity(args)
    BASE.mkdir(parents=True, exist_ok=True)
    report = dict(run_id=RUN_ID, approval=APPROVAL, utc=utc(), identity=identity, preflight=checks,
                  environment=diag.environment(), code_sha256=code_hashes(), python=sys.executable,
                  affinity=sorted(os.sched_getaffinity(0)), memory_address_limit_bytes=MEMORY,
                  free_disk_bytes=shutil.disk_usage(ROOT).free, thread_environment=THREAD_ENV,
                  integration_status='not_run', scientific_admission=False)
    if report['free_disk_bytes'] < 5*1024**3:
        raise RuntimeError('Менее 5 GiB свободного диска')
    save(BASE/'preflight.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return 0


def rss(pid):
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1])*1024
    except FileNotFoundError:
        return 0
    return 0


def output_size(directory):
    return sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())


def supervise_worker(command, directory, *, wall_seconds=5385., memory_bytes=MEMORY,
                     output_bytes=OUTPUT, poll_seconds=1.):
    """Независимый от динамики монитор; timeout снаружи ограничивает всю группу."""
    started = time.monotonic()
    started_utc = utc()
    stop = []
    def on_signal(signum, frame):
        stop.append(f'signal_{signum}')
    old = {s: signal.signal(s, on_signal) for s in (signal.SIGTERM, signal.SIGINT)}
    child = None
    peak_rss = 0
    reason = 'worker_exit'
    try:
        with (directory/'worker.log').open('xb') as log:
            child = subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, **THREAD_ENV),
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            save(directory/'worker_pid.json', dict(pid=child.pid, supervisor_pid=os.getpid(), utc=utc()))
            while child.poll() is None:
                elapsed = time.monotonic()-started
                size = output_size(directory)
                current_rss = rss(child.pid)+rss(os.getpid())
                peak_rss = max(peak_rss, current_rss)
                reason = (stop[0] if stop else
                          'operator_stop' if (directory/'STOP').exists() else
                          'wall_budget' if elapsed >= wall_seconds else
                          'memory_budget' if current_rss >= memory_bytes else
                          'output_budget' if size >= output_bytes-1024**2 else 'worker_exit')
                save(directory/'heartbeat.json', dict(run_id=RUN_ID, utc=utc(), pid=child.pid,
                     supervisor_pid=os.getpid(), elapsed_seconds=elapsed, rss_bytes=current_rss,
                     peak_rss_bytes=peak_rss, output_bytes=size, status='running', stop_reason=reason))
                if reason != 'worker_exit':
                    child.terminate()
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        child.kill()
                    break
                time.sleep(poll_seconds)
            child.wait(timeout=3)
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        for s, handler in old.items():
            signal.signal(s, handler)
        final = dict(run_id=RUN_ID, approval=APPROVAL, started_utc=started_utc, finished_utc=utc(),
                     wall_seconds=time.monotonic()-started, returncode=None if child is None else child.returncode,
                     stop_reason=reason, peak_rss_bytes=peak_rss, output_bytes=output_size(directory),
                     scientific_admission=False, status='finished')
        save(directory/'final.json', final)
        save(directory/'heartbeat.json', final)
    return 0


def launch():
    pre = json.loads((BASE/'preflight.json').read_text(encoding='utf-8'))
    if pre['code_sha256'] != code_hashes() or pre['python'] != sys.executable:
        raise ValueError('Код/Python изменился после preflight')
    timeout = shutil.which('timeout')
    if timeout is None or shutil.disk_usage(ROOT).free < 5*1024**3:
        raise RuntimeError('Нет GNU timeout или места на диске')
    # Это и есть резервирование единственной попытки; каталог не удалять при ошибке.
    ATTEMPT.mkdir(exist_ok=False)
    command = [timeout, '--signal=TERM', '--kill-after=5s', '5395s',
               sys.executable, str(Path(__file__).resolve()), '--supervise']
    manifest = dict(run_id=RUN_ID, approval=APPROVAL, created_utc=utc(), command=command,
                    diagnostic_arguments=arguments(), preflight_sha256=sha(BASE/'preflight.json'),
                    code_sha256=code_hashes(), environment=pre['environment'],
                    max_wall_seconds=5400, worker_wall_seconds=5370,
                    memory_bytes=MEMORY, output_bytes=OUTPUT, workers=1, retries=0,
                    stop_file=(ATTEMPT/'STOP').relative_to(ROOT).as_posix())
    save(ATTEMPT/'launch_manifest.json', manifest)
    with (ATTEMPT/'supervisor.log').open('xb') as log:
        proc = subprocess.Popen(command, cwd=ROOT, env=dict(os.environ, **THREAD_ENV),
                                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)
    save(ATTEMPT/'launcher_pid.json', dict(timeout_pid=proc.pid, utc=utc(), command=command))
    print(json.dumps(dict(run_id=RUN_ID, timeout_pid=proc.pid, directory=str(ATTEMPT)), ensure_ascii=False))
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    for mode in ('preflight', 'launch', 'supervise', 'worker'):
        g.add_argument('--'+mode, action='store_true')
    args = p.parse_args(argv)
    if sys.platform != 'linux' or 'microsoft' not in platform.release().lower():
        raise RuntimeError('Профиль разрешён только локально в WSL')
    if args.preflight:
        return preflight()
    if args.launch:
        return launch()
    manifest = json.loads((ATTEMPT/'launch_manifest.json').read_text(encoding='utf-8'))
    if manifest['code_sha256'] != code_hashes():
        raise ValueError('Код изменился после фиксации запуска')
    if args.supervise:
        # Повторный supervisor также запрещён, независимо от worker marker.
        with (ATTEMPT/'supervisor_started.json').open('x', encoding='utf-8') as handle:
            json.dump(dict(pid=os.getpid(), utc=utc()), handle)
        return supervise_worker([sys.executable, str(Path(__file__).resolve()), '--worker'], ATTEMPT)
    constrain_worker()
    diag = diagnostic()
    if manifest['environment'] != diag.environment():
        raise ValueError('Окружение изменилось после preflight')
    return diag.main(arguments(), capture_approval=APPROVAL)


if __name__ == '__main__':
    raise SystemExit(main())
