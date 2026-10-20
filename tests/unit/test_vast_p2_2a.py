"""W2-T019: сценарий p2_2a транспорта vast_p2 (без сети и аренды)."""
import importlib.util
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest


def load():
    p = Path(__file__).resolve().parents[2]/'scripts'/'vast_p2.py'
    spec = importlib.util.spec_from_file_location('vast_p2', p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rental(**extra):
    return dict(dict(instance_id=55, scenario='p2_2a', offer=dict(cpu_cores_effective=64, cpu_ram=64124),
                     run_deadline_utc='2026-10-16T10:30:00Z', min_workers=28, baseline_margin=1.15), **extra)


def test_p2_2a_script_order_and_commands():
    vast = load()
    script = vast._start_script(rental())
    order = ['import rebound, reboundx', 'pytest -q', 'ruff check', 'hardware.txt', 'fail hardware',
             'admission_p2_2.py --per-cell 4', 'verify_p2_2_run.py --run runs/W2-P2-2a-baseline-55',
             'scripts/baseline_gate_p2_2.py', 'echo "running', 'admission_p2_2.py --per-cell 128',
             '--expect-full --expect-decision-ready', 'echo "done']
    positions = [script.index(x) for x in order]
    assert positions == sorted(positions)
    assert 'test_p2_2_stand.py' in script and 'test_baseline_gate_p2_2.py' in script
    assert '--years 1 10 100 1000 --b-years 100 1000' in script and '--b-per-cell 16' in script
    assert '--deadline-utc 2026-10-16T10:30:00Z --stop-file /workspace/p2_stop --run-dir runs/W2-P2-2a-55' in script
    assert '[ "$W" -ge 28 ] || fail hardware' in script and '--margin 1.15' in script
    assert '|| fail baseline\n' in script and 'admission_p2_horizons' not in script
    assert '--output runs/W2-P2-2a-verification-55/baseline_acceptance.json' in script
    assert '--output runs/W2-P2-2a-verification-55/acceptance.json' in script
    assert '--output runs/W2-P2-2a-55/acceptance.json' not in script
    assert 'workers=$W' in script and 'pin=none seed_rows=0' in script  # формат строки этапа как у P2-1
    if shutil.which('bash'):
        assert subprocess.run(['bash', '-n'], input=script.encode(), check=False).returncode == 0


def test_p2_1_script_unchanged_by_default():
    vast = load()
    old = vast._start_script(dict(instance_id=7, offer=dict(cpu_cores_effective=32, cpu_ram=32000),
                                  run_deadline_utc='2026-10-12T10:00:00Z', pcores_only=False))
    assert 'admission_p2_horizons.py --years 1 10 100 1000 10000' in old and 'admission_p2_2' not in old
    assert vast.run_dir_of(dict(instance_id=7)) == 'runs/W2-P2-1-7'
    assert vast.run_dir_of(rental()) == 'runs/W2-P2-2a-55'
    with pytest.raises(ValueError):
        vast.scenario_of(dict(scenario='p2_9'))


def write_approval(vast, tmp_path, monkeypatch, **extra):
    data = dict(approved=True, user_message='тест', budget_usd=12, max_hourly_usd=0.4, max_hours=22,
                min_effective_cpus=64, cpu_name_regex='5975wx', max_rent_attempts=1, scenario='p2_2a',
                min_workers=28, run_deadline_hours=20.5)
    data.update(extra)
    path = tmp_path/'approval.json'
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}), encoding='utf-8')
    monkeypatch.setattr(vast, 'APPROVAL', path)


def test_approval_requires_p2_2a_fields(tmp_path, monkeypatch):
    vast = load()
    write_approval(vast, tmp_path, monkeypatch)
    assert vast.approval()['scenario'] == 'p2_2a'
    for missing in ('min_workers', 'run_deadline_hours'):
        write_approval(vast, tmp_path, monkeypatch, **{missing: None})
        with pytest.raises(ValueError):
            vast.approval()
    write_approval(vast, tmp_path, monkeypatch, seed=dict(source_jobs='x'))
    with pytest.raises(ValueError):
        vast.approval()
    write_approval(vast, tmp_path, monkeypatch, approved=False)
    with pytest.raises(ValueError):
        vast.approval()


def test_extract_accepts_only_scenario_prefix(tmp_path):
    vast = load()
    archive = tmp_path/'r.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        for name in ('runs/W2-P2-2a-55/jobs.jsonl', 'runs/W2-P2-2a-baseline-55/summary.json',
                     'runs/W2-P2-2a-verification-55/acceptance.json', 'remote_logs/p2_status'):
            data = b'{}\n'
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    root, work = tmp_path/'root', tmp_path/'work'
    stats = vast._extract_results(archive, 55, root=root, work=work, prefix='runs/W2-P2-2a')
    assert stats['written'] == 4 and (root/'runs/W2-P2-2a-baseline-55/summary.json').exists()
    assert (root/'runs/W2-P2-2a-verification-55/acceptance.json').exists()
    with pytest.raises(ValueError):
        vast._extract_results(archive, 55, root=tmp_path/'r2', work=tmp_path/'w2')  # прежний префикс P2-1
