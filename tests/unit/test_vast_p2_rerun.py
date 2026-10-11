"""Перезапуск P2-1 (W2-D021): выборка seed из прежнего jobs.jsonl и режим P-ядер в стартовом сценарии."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest


def load(name):
    p = Path(__file__).resolve().parents[2]/'scripts'/f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(orbit, years, variant, status='completed', fp='x'):
    return json.dumps(dict(orbit_id=orbit, years=years, variant=variant, run_status=status,
                           job_fingerprint=fp+orbit+variant+str(years)))


def test_seed_selects_only_completed_requested_rows():
    vast = load('vast_p2')
    lines = [row('a', 10000.0, 'D'), row('a', 10000.0, 'T'), row('a', 10000.0, 'B'),
             row('a', 1000.0, 'D'), row('b', 10000.0, 'T'), '']
    selected = vast._select_seed(lines, ['D', 'T'], [10000])
    keys = [(json.loads(x)['orbit_id'], json.loads(x)['variant']) for x in selected]
    assert keys == [('a', 'D'), ('a', 'T'), ('b', 'T')]


@pytest.mark.parametrize('bad', [
    row('a', 10000.0, 'D', status='partial'),
    json.dumps(dict(orbit_id='a', years=10000.0, variant='D', run_status='completed', job_fingerprint='')),
])
def test_seed_rejects_incomplete_or_unfingerprinted_rows(bad):
    vast = load('vast_p2')
    with pytest.raises(ValueError):
        vast._select_seed([bad], ['D'], [10000])


def test_seed_rejects_duplicates():
    vast = load('vast_p2')
    with pytest.raises(ValueError):
        vast._select_seed([row('a', 10000.0, 'D'), row('a', 10000.0, 'D')], ['D'], [10000])


def rental(pcores):
    return dict(instance_id=7, offer=dict(cpu_cores_effective=32, cpu_ram=32000),
                run_deadline_utc='2026-10-12T10:00:00Z', pcores_only=pcores)


def test_start_script_pcores_mode_pins_stand_and_fails_without_pcores():
    vast = load('vast_p2')
    pinned, plain = vast._start_script(rental(True)), vast._start_script(rental(False))
    assert '/sys/devices/cpu_core/cpus' in pinned and 'fail pcores' in pinned
    assert '$PIN .venv/bin/python -u scripts/admission_p2_horizons.py' in pinned
    assert 'taskset' not in plain and 'PIN=""' in plain
    for script in (pinned, plain):
        assert 'diag_ias15_guard_dtdone.py' in script and 'seed_rows=$SEED' in script
        if shutil.which('bash'):
            assert subprocess.run(['bash', '-n'], input=script.encode(), check=False).returncode == 0
