"""Негативные проверки read-only входа; физические интеграции не выполняются."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from submoon_research.diagnostics import phase3_v2_input as inp

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'results/evidence/W2_phase3_audit_20261030/v1/audit.json'
REPORT_SHA = '0febe080e713cca4ea7cd6530beb46b7a89aa3f3fcff9b6bb17d69769e236150'


@pytest.fixture(scope='module')
def snapshot():
    if not REPORT.exists():
        pytest.skip('Закреплённый аудит и runs отсутствуют')
    baseline = inp._json(REPORT.read_bytes())
    return inp.capture(ROOT, baseline['inventory'])


def test_real_sealed_snapshot(snapshot, monkeypatch):
    monkeypatch.setattr(inp, 'capture', lambda *args: snapshot)
    result = inp.load(ROOT, REPORT, REPORT_SHA)
    assert result['rows'] == 439 and len(result['cases']) == 16
    assert sum(c['archive_b'] is not None for c in result['cases']) == 13
    candidate = next(c for c in result['cases'] if c['orbit_id'] == 'himalia:uniform_a_e_cos_i_phases:0012')
    assert candidate['archive_b']['outcome'] == 'host_contact'
    assert len(candidate['case']['perturb_rows']) == 30


def test_bad_external_sha():
    if not REPORT.exists():
        pytest.skip('Закреплённый аудит отсутствует')
    with pytest.raises(ValueError, match='Внешний SHA'):
        inp.load(ROOT, REPORT, '0' * 64)


def test_capture_tamper_missing_and_path_escape(tmp_path):
    file = tmp_path / 'test.bin'
    file.write_bytes(b'correct')
    entry = {'sha256': hashlib.sha256(b'correct').hexdigest(), 'bytes': 7}
    assert inp.capture(tmp_path, {'test.bin': entry})['test.bin'] == b'correct'
    file.write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA/размера'):
        inp.capture(tmp_path, {'test.bin': entry})
    with pytest.raises(FileNotFoundError):
        inp.capture(tmp_path, {'missing': entry})
    with pytest.raises(ValueError, match='Недопустимый путь'):
        inp.capture(tmp_path, {'../other': entry})


def test_duplicate_missing_extra_jobs(snapshot, monkeypatch):
    path = inp.RUNS['P0'] + '/jobs.jsonl'
    lines = snapshot[path].splitlines()
    for changed in (lines[:-1], lines + [lines[0]], lines[:-1] + [lines[0]]):
        altered = dict(snapshot)
        altered[path] = b'\n'.join(changed) + b'\n'
        monkeypatch.setattr(inp, 'capture', lambda *args, s=altered: s)
        with pytest.raises(ValueError, match='Пропуск, повтор или чужое задание'):
            inp.load(ROOT, REPORT, REPORT_SHA)


def test_manifest_sha_and_unlisted_file(snapshot, monkeypatch):
    path = inp.RUNS['P0'] + '/manifest.json'
    changed = dict(snapshot)
    changed[path] += b' '
    monkeypatch.setattr(inp, 'capture', lambda *args: changed)
    with pytest.raises(ValueError, match='SHA manifest'):
        inp.load(ROOT, REPORT, REPORT_SHA)
    changed = dict(snapshot)
    del changed[path]
    monkeypatch.setattr(inp, 'capture', lambda *args: changed)
    with pytest.raises(ValueError, match='отсутствует в закреплённом инвентаре'):
        inp.load(ROOT, REPORT, REPORT_SHA)


def test_sample_tamper_and_row_substitution(snapshot, monkeypatch):
    run = inp.RUNS['P0']
    rows = inp._jsonl(snapshot[run + '/jobs.jsonl'])
    sample = rows[0]['samples_file']
    changed = dict(snapshot)
    changed[sample] += b'x'
    monkeypatch.setattr(inp, 'capture', lambda *args: changed)
    with pytest.raises(ValueError, match='SHA выборки'):
        inp.load(ROOT, REPORT, REPORT_SHA)
    changed = dict(snapshot)
    rows[0]['code'] = 'unexpected'
    changed[run + '/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    monkeypatch.setattr(inp, 'capture', lambda *args: changed)
    with pytest.raises(ValueError, match='не соответствует fingerprint/плану'):
        inp.load(ROOT, REPORT, REPORT_SHA)


def test_phase3_contact_body_mismatch(snapshot, monkeypatch):
    run = inp.RUNS['P0']
    rows = inp._jsonl(snapshot[run + '/jobs.jsonl'])
    contact = next(r for r in rows if r['outcome'] == 'host_contact')
    contact['outcome'] = 'other_contact'
    assert contact['event']['body'] == 0
    altered = dict(snapshot)
    altered[run + '/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    monkeypatch.setattr(inp, 'capture', lambda *args: altered)
    with pytest.raises(ValueError, match='Неверное тело контакта'):
        inp.load(ROOT, REPORT, REPORT_SHA)


def test_archive_b_identity_mismatch(snapshot, monkeypatch):
    original = inp._archive

    def bad_archive(*args):
        units, rows = original(*args)
        units = copy.deepcopy(units)
        units['himalia:uniform_a_e_cos_i_phases:0012']['gms'][0] *= 1.01
        return units, rows

    monkeypatch.setattr(inp, 'capture', lambda *args: snapshot)
    monkeypatch.setattr(inp, '_archive', bad_archive)
    with pytest.raises(ValueError, match='старт/физика отличаются'):
        inp.load(ROOT, REPORT, REPORT_SHA)


def test_b_archive_duplicate_and_bad_version(snapshot):
    changed = dict(snapshot)
    b = 'runs/W2-B144-57496493'
    changed[b + '/jobs.jsonl'] += snapshot[b + '/jobs.jsonl'].splitlines()[0] + b'\n'
    cfg = inp.yaml.safe_load(snapshot[inp.audit.CONFIG].decode('utf-8'))
    cfg['archives']['b144']['jobs_sha256'] = inp._hash(changed[b + '/jobs.jsonl'])
    with pytest.raises(ValueError, match='неполный план или дубли'):
        inp._archive(changed, cfg)
    changed = dict(snapshot)
    m = inp._json(changed[b + '/manifest.json'])
    m['b_implementation'] = '0' * 64
    changed[b + '/manifest.json'] = json.dumps(m).encode()
    cfg['archives']['b144']['manifest_sha256'] = inp._hash(changed[b + '/manifest.json'])
    with pytest.raises(ValueError, match='версия реализации'):
        inp._archive(changed, cfg)
