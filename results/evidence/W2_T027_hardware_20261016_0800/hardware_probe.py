"""Bounded, non-scientific hardware probe; no secrets or project data required."""
import concurrent.futures
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


def text(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def command(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=15)
        return {"returncode": r.returncode, "stdout": r.stdout[:16000], "stderr": r.stderr[:2000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": type(exc).__name__}


def worker(args):
    cpu, duration = args
    os.sched_setaffinity(0, {cpu})
    start, cpu_start = time.monotonic(), time.process_time()
    n, value = 0, cpu + 1.0
    while time.monotonic() - start < duration:
        for _ in range(4096):
            value = (value * 1.0000001 + 0.1234567) % 10000.0
        n += 4096
    return {"cpu": cpu, "iterations": n, "wall_seconds": time.monotonic() - start,
            "cpu_seconds": time.process_time() - cpu_start, "finite_value": value < 10000.0}


def load(cpus, seconds):
    before = text('/sys/fs/cgroup/cpu.stat')
    start = time.monotonic()
    ctx = multiprocessing.get_context('fork')
    with concurrent.futures.ProcessPoolExecutor(max_workers=len(cpus), mp_context=ctx) as pool:
        rows = list(pool.map(worker, [(cpu, seconds) for cpu in cpus]))
    wall = time.monotonic() - start
    return {"workers": len(cpus), "wall_seconds": wall, "rows": rows,
            "sum_cpu_seconds": sum(x['cpu_seconds'] for x in rows),
            "iterations_per_second": sum(x['iterations'] for x in rows) / wall,
            "cpu_stat_before": before, "cpu_stat_after": text('/sys/fs/cgroup/cpu.stat')}


def main():
    result = {"utc_epoch": time.time(), "uname": list(os.uname()),
              "python": command(['python3', '--version']),
              "lscpu": command(['lscpu', '-J']),
              "cgroup_membership": text('/proc/self/cgroup'),
              "meminfo": text('/proc/meminfo'),
              "cpuinfo_first": (text('/proc/cpuinfo') or '').split('\n\n')[0],
              "mountinfo": text('/proc/self/mountinfo'),
              "processes_before": command(['ps', '-eo', 'pid,comm,pcpu,pmem']),
              "gpu": command(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader'])}
    cg = Path('/sys/fs/cgroup')
    names = ('cpu.max', 'cpu.stat', 'cpuset.cpus.effective', 'memory.max', 'memory.current',
             'memory.events', 'cpu/cpu.cfs_quota_us', 'cpu/cpu.cfs_period_us',
             'cpuset/cpuset.cpus', 'memory/memory.limit_in_bytes')
    result['cgroup'] = {name: text(cg / name) for name in names}
    cpus = sorted(os.sched_getaffinity(0))
    physical = {}
    for cpu in cpus:
        root = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        key = (text(root / 'physical_package_id'), text(root / 'core_id'))
        physical.setdefault(key, []).append(cpu)
    result['affinity'] = cpus
    result['physical_groups'] = [{'package': k[0], 'core': k[1], 'threads': v} for k, v in physical.items()]
    result['single_core'] = load(cpus[:1], 4)
    result['physical_core_load'] = load([v[0] for v in physical.values()][:64], 15)
    result['logical_core_load'] = load(cpus[:128], 15)
    start = time.monotonic()
    size = 512 * 1024 * 1024
    data = bytearray(size)
    pages = size // 4096
    for i in range(pages):
        data[i * 4096] = i % 251
    valid = all(data[i * 4096] == i % 251 for i in range(pages))
    result['memory_probe'] = {'bytes': size, 'pages_written_and_verified': pages,
                              'passed': valid, 'seconds': time.monotonic() - start}
    del data
    root = '/workspace' if Path('/workspace').is_dir() else '/tmp'
    result['filesystem'] = command(['df', '-B1', root])
    result['disk_usage'] = dict(zip(('total', 'used', 'free'), shutil.disk_usage(root)))
    block = os.urandom(1024 * 1024)
    expected = hashlib.sha256()
    with tempfile.TemporaryDirectory(prefix='submoon-hardware-', dir=root) as folder:
        path = Path(folder) / 'probe.bin'
        start = time.monotonic()
        with path.open('xb') as stream:
            for _ in range(64):
                stream.write(block)
                expected.update(block)
            stream.flush()
            os.fsync(stream.fileno())
        write_seconds = time.monotonic() - start
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(chunk)
        result['disk_probe'] = {'bytes': 64 * 1024 * 1024, 'fsync_write_seconds': write_seconds,
                                'sha256': actual.hexdigest(), 'passed': actual.digest() == expected.digest(),
                                'read_is_cached_not_bandwidth_benchmark': True}
    result['memory_events_after'] = text(cg / 'memory.events')
    result['completed_epoch'] = time.time()
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
