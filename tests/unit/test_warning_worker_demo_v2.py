"""F1/F2: изолированный v2; fault injection не заменяет real kill/concurrency."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from submoon_research.diagnostics import warning_worker_demo_v2 as demo

CONTRACT = 'demo-f1f2-fingerprint'
ROOT = Path(__file__).resolve().parents[2]


def run(tmp_path, mode, parent=None, parent_sha=None, name='job', **capture):
    directory = tmp_path / name
    args = [sys.executable, '-m', 'submoon_research.diagnostics.warning_worker_demo_v2',
            '--directory', str(directory), '--contract', CONTRACT, '--mode', mode]
    if parent:
        args.extend(['--parent', str(parent), '--parent-sha', parent_sha])
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'))
    if not capture:
        capture = dict(capture_output=True)
    proc = subprocess.run(args, timeout=30, env=env, check=False, **capture)
    result = demo.evaluate(directory, CONTRACT, proc.returncode, proc.stdout, proc.stderr)
    return directory, proc, result


@pytest.fixture
def clean(tmp_path):
    directory, proc, result = run(tmp_path, 'clean')
    assert proc.returncode == 0 and result['admissible']
    assert demo.read_json(directory / 'receipt.json')['schema'] == demo.SCHEMA
    return directory


@pytest.mark.parametrize('stdout,stderr', [(b'', b''), ('', ''), (b'', ''), ('', b'')])
def test_explicit_empty_capture_preserves_synthetic_outcome(clean, stdout, stderr):
    result = demo.evaluate(clean, CONTRACT, 0, stdout, stderr)
    assert result == dict(admissible=True, outcome='preserve_synthetic_outcome', reasons=[])


@pytest.mark.parametrize('stdout,stderr', [(None, None), (None, b''), (b'', None),
                                         (None, ''), ('', None)])
def test_missing_capture_is_unknown(clean, stdout, stderr):
    result = demo.evaluate(clean, CONTRACT, 0, stdout, stderr)
    assert result == dict(admissible=False, outcome='unresolved', reasons=['output_capture_unknown'])


@pytest.mark.parametrize('value', [False, 0, [], {}, bytearray(), memoryview(b'')])
@pytest.mark.parametrize('channel', ['stdout', 'stderr'])
def test_unsupported_falsy_capture_is_not_empty(clean, channel, value):
    streams = dict(stdout=b'', stderr=b'')
    streams[channel] = value
    result = demo.evaluate(clean, CONTRACT, 0, **streams)
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert 'output_capture_unknown' in result['reasons']


@pytest.mark.parametrize('stdout,stderr', [(b'diagnostic', b''), (b'', b'diagnostic'),
                                         ('diagnostic', ''), ('', 'diagnostic')])
def test_nonempty_captured_output_is_rejected(clean, stdout, stderr):
    result = demo.evaluate(clean, CONTRACT, 0, stdout, stderr)
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert result['reasons'] == ['unclassified_output']


def test_missing_stream_does_not_hide_known_nonempty_stream(clean):
    result = demo.evaluate(clean, CONTRACT, 0, None, b'diagnostic')
    assert result['reasons'] == ['output_capture_unknown', 'unclassified_output']
    assert not result['admissible']


def test_actual_stderr_without_capture_cannot_be_accepted(tmp_path):
    # Реальный os.write(2,...); вывод сохранён вне CompletedProcess, а не выдуман.
    log = tmp_path / 'actual_stderr.txt'
    with log.open('wb') as stream:
        directory, proc, result = run(tmp_path, 'stderr', stdout=subprocess.DEVNULL, stderr=stream)
    assert proc.returncode == 0 and proc.stdout is None and proc.stderr is None
    assert log.read_bytes() == b'native diagnostic on stderr\n'
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert 'output_capture_unknown' in result['reasons']
    known = demo.evaluate(directory, CONTRACT, 0, b'', log.read_bytes())
    assert known['reasons'] == ['unclassified_output']


@pytest.mark.parametrize('mode,reason', [('warning', 'warnings_present'), ('partial_warning', 'partial'),
                                        ('overflow', 'overflow'), ('stderr', 'unclassified_output'),
                                        ('crash_warning', 'capture_incomplete'),
                                        ('crash_before_checkpoint', 'capture_incomplete'),
                                        ('crash_after_checkpoint', 'capture_incomplete'),
                                        ('exception', 'capture_incomplete')])
def test_existing_fail_closed_modes(tmp_path, mode, reason):
    _, _, result = run(tmp_path, mode)
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert reason in result['reasons']


def test_prior_warning_sticky_and_checkpoint_integrity(tmp_path):
    parent, _, old = run(tmp_path, 'partial_warning', name='parent')
    assert 'warnings_present' in old['reasons']
    cp = parent / 'checkpoint.json'
    pinned = hashlib.sha256(cp.read_bytes()).hexdigest()
    child, proc, result = run(tmp_path, 'clean', cp, pinned, 'resumed')
    assert proc.returncode == 0 and 'warnings_present' in result['reasons']
    assert demo.read_json(child / 'receipt.json')['warning_count'] == 1
    cp.write_bytes(cp.read_bytes() + b' ')
    _, proc, result = run(tmp_path, 'clean', cp, pinned, 'bad')
    assert proc.returncode != 0 and not result['admissible']


def test_terminal_checkpoint_cannot_resume(tmp_path):
    parent, _, _ = run(tmp_path, 'clean', name='parent')
    cp = parent / 'checkpoint.json'
    _, proc, result = run(tmp_path, 'clean', cp, demo.sha(cp.read_bytes()), 'resumed')
    assert proc.returncode != 0 and not result['admissible']


def test_truncated_and_missing_artifacts_fail_closed(tmp_path):
    directory, proc, _ = run(tmp_path, 'warning')
    journal = directory / 'warnings.jsonl'
    journal.write_bytes(journal.read_bytes().rstrip(b'\n'))
    assert 'capture_incomplete' in demo.evaluate(directory, CONTRACT, proc.returncode,
                                                proc.stdout, proc.stderr)['reasons']
    (directory / 'receipt.json').unlink()
    assert not demo.evaluate(directory, CONTRACT, 0, b'', b'')['admissible']


def checkpoint(events, status='partial'):
    return dict(schema=demo.SCHEMA, contract=CONTRACT, physical_state={'synthetic_time': 1.0},
                events=events, overflow=False, capture_complete=True, status=status)


def event():
    return dict(sequence=1, utc='2026-11-01T00:00:00+00:00', pid=123,
                category='RuntimeWarning', message='synthetic inherited warning', phase='synthetic',
                model_time=None, filename='synthetic.py', line=1)


def replace_after_read(monkeypatch, target, replacement):
    original = Path.read_bytes
    reads = []

    def read(path):
        raw = original(path)
        if path == target:
            reads.append(raw)
            assert len(reads) == 1, 'checkpoint must be read once per verification'
            path.write_bytes(replacement)
        return raw

    monkeypatch.setattr(Path, 'read_bytes', read)
    return reads, original


@pytest.mark.parametrize('warnings_first', [True, False])
def test_verified_document_is_the_hashed_snapshot(tmp_path, monkeypatch, warnings_first):
    first = checkpoint([event()] if warnings_first else [])
    second = checkpoint([] if warnings_first else [event()])
    raw, replacement = demo.encode(first), demo.encode(second)
    cp = tmp_path / 'checkpoint.json'
    cp.write_bytes(raw)
    reads, original = replace_after_read(monkeypatch, cp, replacement)
    doc = demo.verify_checkpoint(cp, demo.sha(raw), CONTRACT)
    assert reads == [raw] and doc == first
    assert original(cp) == replacement and demo.sha(original(cp)) != demo.sha(raw)
    # Возвращён проверенный снимок, не гарантия неизменности пути после возврата.


def test_evaluate_cannot_accept_clean_replacement_for_pinned_warning(tmp_path, monkeypatch):
    directory = tmp_path / 'job'
    directory.mkdir()
    cp = directory / 'checkpoint.json'
    raw = demo.encode(checkpoint([event()], 'completed'))
    cp.write_bytes(raw)
    (directory / 'warnings.jsonl').write_bytes(b'')
    receipt = dict(schema=demo.SCHEMA, contract=CONTRACT, status='completed',
                   journal_sha256=demo.sha(b''), checkpoint_sha256=demo.sha(raw),
                   warning_count=0, overflow=False, capture_complete=True)
    (directory / 'receipt.json').write_bytes(demo.encode(receipt))
    reads, _ = replace_after_read(monkeypatch, cp, demo.encode(checkpoint([], 'completed')))
    result = demo.evaluate(directory, CONTRACT, 0, b'', b'')
    assert len(reads) == 1
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert 'capture_incomplete' in result['reasons']


def test_resume_keeps_warning_from_pinned_snapshot(tmp_path, monkeypatch):
    cp = tmp_path / 'parent.json'
    raw = demo.encode(checkpoint([event()]))
    cp.write_bytes(raw)
    reads, _ = replace_after_read(monkeypatch, cp, demo.encode(checkpoint([])))
    child = tmp_path / 'resumed'
    demo.worker(child, CONTRACT, 'clean', cp, demo.sha(raw))
    assert reads == [raw]
    result = demo.evaluate(child, CONTRACT, 0, b'', b'')
    assert not result['admissible'] and 'warnings_present' in result['reasons']
    assert demo.read_json(child / 'checkpoint.json')['events'] == [event()]


@pytest.mark.parametrize('raw', [b'{"a":1,"a":2}', b'{"nested":{"a":1,"a":2}}',
                               b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}',
                               b'{"a":"\xff"}', b'{', b'{} {}'])
def test_hashed_malformed_json_is_still_rejected(tmp_path, raw):
    cp = tmp_path / 'checkpoint.json'
    cp.write_bytes(raw)
    with pytest.raises((ValueError, UnicodeError)):
        demo.verify_checkpoint(cp, demo.sha(raw), CONTRACT)


def test_hash_is_checked_before_json_parsing(tmp_path, monkeypatch):
    cp = tmp_path / 'checkpoint.json'
    cp.write_bytes(b'not JSON')

    def forbidden(raw):
        raise AssertionError('must not parse unverified bytes')

    monkeypatch.setattr(demo, 'parse_json', forbidden)
    with pytest.raises(ValueError, match='checkpoint hash mismatch'):
        demo.verify_checkpoint(cp, '0'*64, CONTRACT)


def test_v1_checkpoint_is_incompatible(tmp_path):
    cp = tmp_path / 'checkpoint.json'
    old = dict(checkpoint([]), schema='W2-warning-worker-demo-v1')
    cp.write_bytes(demo.encode(old))
    with pytest.raises(ValueError, match='invalid checkpoint'):
        demo.verify_checkpoint(cp, demo.sha(cp.read_bytes()), CONTRACT)


def test_contract_mismatch_is_rejected(tmp_path):
    cp = tmp_path / 'checkpoint.json'
    cp.write_bytes(demo.encode(checkpoint([])))
    with pytest.raises(ValueError, match='invalid checkpoint'):
        demo.verify_checkpoint(cp, demo.sha(cp.read_bytes()), 'other-contract')


def test_snapshot_round_trip_preserves_unicode(tmp_path):
    cp = tmp_path / 'checkpoint.json'
    warning = dict(event(), message='Предупреждение: синтетический контроль')
    raw = demo.encode(checkpoint([warning]))
    cp.write_bytes(raw)
    assert demo.verify_checkpoint(cp, demo.sha(raw), CONTRACT) == json.loads(raw)
