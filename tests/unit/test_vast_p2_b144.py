"""W2-T050: scenario b144 of the vast_p2 transport (no network, no rental)."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

EXPECTED = '0f5ef90438a14a7f2e5e3ad835b32cf28e645ec89227c78f8fa784ffce2b4c64'
REFERENCE = '088ecc0d25d82377d90ca48a1f6b1d5b4959560814308e8e65144ddf614140f7'


def load():
    p = Path(__file__).resolve().parents[2]/'scripts'/'vast_p2.py'
    spec = importlib.util.spec_from_file_location('vast_p2', p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rental(**extra):
    return dict(dict(instance_id=55, scenario='b144', offer=dict(cpu_cores_effective=64, cpu_ram=257061),
                     run_deadline_utc='2026-10-22T14:00:00Z', min_workers=28, benchmark_margin=1.25,
                     expected_b_fingerprint=EXPECTED, reference_sha256=REFERENCE), **extra)


def test_b144_script_order_and_pins():
    vast = load()
    script = vast._start_script(rental())
    order = ['import rebound, reboundx', 'pytest -q', 'ruff check', 'hardware.txt', 'fail hardware',
             'fail avx512', 'export OPENBLAS_CORETYPE=SkylakeX', 'b144_regress.py prepare', 'fail inputs',
             'b144_regress.py benchmark', 'fail benchmark', 'echo "running', 'b144_regress.py run', 'echo "done']
    positions = [script.index(x) for x in order]
    assert positions == sorted(positions)
    assert f'--expected-b {EXPECTED} --reference-sha256 {REFERENCE}' in script
    assert '--run-dir runs/W2-B144-55' in script and 'runs/W2-B144-logs-55' in script
    assert '--deadline-utc 2026-10-22T14:00:00Z --stop-file /workspace/p2_stop' in script
    assert '[ "$W" -ge 28 ] || fail hardware' in script and '--margin 1.25' in script
    assert 'test_b144_regress.py' in script and 'test_ias15_cm_start.py' in script
    assert 'admission_p2_2.py' not in script and 'admission_p2_horizons.py' not in script
    assert 'workers=$W' in script and 'pin=none seed_rows=0' in script
    assert vast.run_dir_of(rental()) == 'runs/W2-B144-55'
    # Не повторять Windows CreateProcess lookup по bare name: он может выбрать WSL.
    bash = shutil.which('bash')
    if bash:
        assert subprocess.run([bash, '-n'], input=script.encode(), check=False, timeout=10).returncode == 0


def test_b144_script_requires_full_hashes():
    vast = load()
    with pytest.raises(ValueError):
        vast._start_script(rental(expected_b_fingerprint='0f5ef904'))


def write_approval(vast, tmp_path, monkeypatch, **extra):
    data = dict(approved=True, user_message='тест', budget_usd=8, max_hourly_usd=0.7, max_hours=8,
                min_effective_cpus=64, cpu_name_regex='9975WX', max_rent_attempts=1, scenario='b144',
                min_workers=28, run_deadline_hours=6.5, expected_b_fingerprint=EXPECTED, reference_sha256=REFERENCE)
    data.update(extra)
    path = tmp_path/'approval.json'
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not None}), encoding='utf-8')
    monkeypatch.setattr(vast, 'APPROVAL', path)


def test_approval_requires_b144_fields(tmp_path, monkeypatch):
    vast = load()
    write_approval(vast, tmp_path, monkeypatch)
    assert vast.approval()['scenario'] == 'b144'
    for missing in ('min_workers', 'run_deadline_hours', 'expected_b_fingerprint', 'reference_sha256'):
        write_approval(vast, tmp_path, monkeypatch, **{missing: None})
        with pytest.raises(ValueError):
            vast.approval()
    write_approval(vast, tmp_path, monkeypatch, reference_sha256='088ecc0d')
    with pytest.raises(ValueError):
        vast.approval()
    write_approval(vast, tmp_path, monkeypatch, pcores_only=True)
    with pytest.raises(ValueError):
        vast.approval()


def test_draft_approval_is_not_approved_and_consistent():
    root = Path(__file__).resolve().parents[2]
    draft = root/'docs/compute/W2_B144_approval_draft.json'
    data = json.loads(draft.read_text(encoding='utf-8'))
    assert data['approved'] is False and data['scenario'] == 'b144'
    assert data['expected_b_fingerprint'] == EXPECTED and data['reference_sha256'] == REFERENCE
    assert float(data['max_hourly_usd'])*float(data['max_hours']) <= float(data['budget_usd'])
    assert data['run_deadline_hours'] < data['max_hours'] and data['max_rent_attempts'] == 1


def test_destroy_refuses_personal_instance(tmp_path, monkeypatch):
    vast = load()
    path = tmp_path/'rental.json'
    path.write_text(json.dumps(dict(instance_id=56229721, created_epoch=0, offer=dict(dph_total=0.1))), encoding='utf-8')
    monkeypatch.setattr(vast, 'RENTAL', path)
    monkeypatch.setattr(vast.vw, 'cli', lambda *a, **k: pytest.fail('cli must not be called'))
    with pytest.raises(RuntimeError, match='W2-D035'):
        vast.destroy()
