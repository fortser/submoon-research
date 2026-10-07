"""Проверка загруженного снимка и реального ресурса до научного исполнения."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.workflows.w2_campaign import machine_info, worker_limit  # noqa: E402


def main():
    manifest = json.loads((ROOT/'bundle_manifest.json').read_text(encoding='utf-8'))
    failures = []
    for name, digest in manifest['files'].items():
        # Editable installation rewrites this generated packaging metadata.
        if name.startswith('src/submoon_research.egg-info/'):
            continue
        path = ROOT/name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(name)
    machine = machine_info()
    available = psutil.virtual_memory().available
    quota = machine.get('memory.max', 'max')
    if quota != 'max':
        available = min(available, int(quota))
    checks = dict(snapshot_sha256=not failures, free_disk=shutil.disk_usage(ROOT).free >= 5*2**30,
        memory_at_least_8_gib=available >= 8*2**30, cpu_quota_at_least_two=worker_limit(16) >= 2,
        remote_explicit=os.environ.get('W2_REMOTE_EXECUTION') == '1')
    result = dict(checks=checks, machine=machine, memory_available=available,
                  max_workers=worker_limit(16), snapshot_failures=failures)
    target = ROOT/'scratch/w2_preflight.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if all(checks.values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
