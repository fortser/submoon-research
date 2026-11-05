"""Ограниченный Linux probe без научного пакета/сети; запускается только удалённо.

Не проверяет всю RAM/VRAM, эксклюзивность хоста или контрактную квоту диска.
"""
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
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
        p = subprocess.run(args, capture_output=True, text=True, timeout=10,
                           env=dict(os.environ, LC_ALL='C'))
        return dict(returncode=p.returncode, stdout=p.stdout[:16000])
    except (OSError, subprocess.TimeoutExpired):
        return dict(returncode=None, stdout=None)


def worker(args):
    cpu, seconds = args
    os.sched_setaffinity(0, {cpu})
    start, cpu_start = time.monotonic(), time.process_time()
    value, n = cpu + 1.0, 0
    while time.monotonic() - start < seconds:
        for _ in range(4096):
            value = (value * 1.0000001 + .1234567) % 10000
        n += 4096
    return dict(cpu=cpu, iterations=n, wall_seconds=time.monotonic() - start,
                cpu_seconds=time.process_time() - cpu_start, finite=math.isfinite(value))


def load(cpus, seconds):
    start = time.monotonic()
    before = text('/sys/fs/cgroup/cpu.stat')
    with ProcessPoolExecutor(max_workers=len(cpus), mp_context=multiprocessing.get_context('fork')) as pool:
        rows = list(pool.map(worker, [(cpu, seconds) for cpu in cpus]))
    wall = time.monotonic() - start
    return dict(workers=len(cpus), duration_per_worker_seconds=seconds, wall_seconds=wall,
                rows=rows, sum_cpu_seconds=sum(r['cpu_seconds'] for r in rows),
                equivalent_busy_cpus=sum(r['cpu_seconds'] for r in rows) / wall,
                cpu_stat_before=before, cpu_stat_after=text('/sys/fs/cgroup/cpu.stat'))


def capacity_check(physical, logical):
    return bool(physical is not None and
                physical['equivalent_busy_cpus'] >= .85 * physical['workers'] and
                logical['equivalent_busy_cpus'] >= .85 * logical['workers'])


def main():
    start = time.time()
    cpus = sorted(os.sched_getaffinity(0))
    groups = {}
    topology_known = True
    for cpu in cpus:
        base = Path(f'/sys/devices/system/cpu/cpu{cpu}/topology')
        package, core = text(base / 'physical_package_id'), text(base / 'core_id')
        if package is None or core is None:
            topology_known = False
            continue
        groups.setdefault((package, core), []).append(cpu)
    names = ('cpu.max', 'cpu.stat', 'memory.max', 'memory.current', 'memory.events',
             'cpuset.cpus.effective', 'cpu/cpu.cfs_quota_us', 'cpu/cpu.cfs_period_us',
             'memory/memory.limit_in_bytes')
    data = dict(schema_version=1, started_epoch=start, python=command(['python3', '--version']),
                uname=list(os.uname()), lscpu=command(['lscpu', '-J']), affinity=cpus,
                topology_known=topology_known,
                physical_groups=[dict(package=k[0], core=k[1], threads=v) for k, v in groups.items()],
                cgroup={name: text(Path('/sys/fs/cgroup') / name) for name in names},
                cgroup_membership=text('/proc/self/cgroup'), meminfo=text('/proc/meminfo'),
                gpu_inventory=command(['nvidia-smi', '--query-gpu=name,memory.total,driver_version',
                                       '--format=csv,noheader']))
    data['single_core'] = load(cpus[:1], 4)
    data['physical_core_load'] = load([v[0] for v in groups.values()][:96], 30) if groups else None
    data['logical_core_load'] = load(cpus[:192], 30)
    memory = bytearray(512 * 1024 * 1024)
    for i in range(0, len(memory), 4096):
        memory[i] = (i // 4096) % 251
    memory_ok = all(memory[i] == (i // 4096) % 251 for i in range(0, len(memory), 4096))
    data['memory_probe'] = dict(bytes=len(memory), pages=len(memory) // 4096, passed=memory_ok)
    del memory
    root = '/workspace' if Path('/workspace').is_dir() else '/tmp'
    data['filesystem'] = command(['df', '-B1', root])
    data['disk_usage_host_filesystem_not_contract_quota'] = dict(zip(('total', 'used', 'free'), shutil.disk_usage(root)))
    block = os.urandom(1024 * 1024)
    expected = hashlib.sha256(block * 64).hexdigest()
    with tempfile.TemporaryDirectory(prefix='submoon-probe-', dir=root) as directory:
        path = Path(directory) / 'probe.bin'
        t0 = time.monotonic()
        with path.open('xb') as stream:
            for _ in range(64):
                stream.write(block)
            stream.flush()
            os.fsync(stream.fileno())
        write_seconds = time.monotonic() - t0
        actual = hashlib.sha256()
        with path.open('rb') as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(part)
        data['disk_probe'] = dict(bytes=64 * 1024 * 1024, fsync_write_seconds=write_seconds,
                                  passed=actual.hexdigest() == expected, sha256=actual.hexdigest())
    cpu_ok = all(r['finite'] and r['cpu_seconds'] > 0 for r in data['logical_core_load']['rows'])
    physical = data['physical_core_load']
    logical = data['logical_core_load']
    capacity_ok = capacity_check(physical, logical)
    data['cpu_capacity_sample_min_fraction'] = .85  # операционный sanity gate, не гарантия эксклюзивности
    data['checks'] = dict(cpu_work=cpu_ok, cpu_capacity_sample=capacity_ok,
                          memory_sample=memory_ok, disk_sample=data['disk_probe']['passed'])
    data['limitations'] = ['sampled_RAM_not_full_test', 'no_VRAM_load_test', 'no_exclusivity_guarantee',
                          'disk_read_may_be_cached', 'filesystem_capacity_not_contract_quota',
                          'parent_cgroup_limits_may_be_hidden', 'physical_load_cap_96_logical_cap_192']
    data.update(complete=True, completed_epoch=time.time())
    print(json.dumps(data, allow_nan=False), flush=True)


if __name__ == '__main__':
    main()
