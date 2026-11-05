"""Процессные и аварийные тесты синтетического worker, не production."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from submoon_research.diagnostics.warning_worker_demo import evaluate

CONTRACT = 'demo-fingerprint'
ROOT = Path(__file__).resolve().parents[2]


def run(tmp_path, mode, parent=None, parent_sha=None, name='job'):
    directory = tmp_path / name
    args = [sys.executable, '-m', 'submoon_research.diagnostics.warning_worker_demo',
            '--directory', str(directory), '--contract', CONTRACT, '--mode', mode]
    if parent:
        args.extend(['--parent', str(parent), '--parent-sha', parent_sha])
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'src')
    process = subprocess.run(args, capture_output=True, timeout=30, env=env, check=False)
    return directory, process, evaluate(directory, CONTRACT, process.returncode, process.stdout, process.stderr)


def test_clean_separate_worker(tmp_path):
    directory, process, result = run(tmp_path, 'clean')
    assert process.returncode == 0 and result['admissible']
    assert json.loads((directory / 'receipt.json').read_text())['warning_count'] == 0


@pytest.mark.parametrize('mode,reason', [('warning', 'warnings_present'), ('partial_warning', 'partial'),
                                        ('overflow', 'overflow'), ('stderr', 'unclassified_output'),
                                        ('crash_warning', 'capture_incomplete'),
                                        ('crash_before_checkpoint', 'capture_incomplete'),
                                        ('crash_after_checkpoint', 'capture_incomplete'),
                                        ('exception', 'capture_incomplete')])
def test_fail_closed(tmp_path, mode, reason):
    _, _, result = run(tmp_path, mode)
    assert not result['admissible'] and result['outcome'] == 'unresolved'
    assert reason in result['reasons']


def test_prior_warning_sticky_and_checkpoint_integrity(tmp_path):
    parent, _, old = run(tmp_path, 'partial_warning', name='parent')
    assert 'warnings_present' in old['reasons']
    cp = parent / 'checkpoint.json'
    pinned = hashlib.sha256(cp.read_bytes()).hexdigest()
    child, process, result = run(tmp_path, 'clean', cp, pinned, 'resumed')
    assert process.returncode == 0 and 'warnings_present' in result['reasons']
    assert json.loads((child / 'receipt.json').read_text())['warning_count'] == 1
    cp.write_bytes(cp.read_bytes() + b' ')
    _, process, result = run(tmp_path, 'clean', cp, pinned, 'bad')
    assert process.returncode != 0 and not result['admissible']


def test_terminal_checkpoint_cannot_resume(tmp_path):
    parent, _, _ = run(tmp_path, 'clean', name='parent')
    cp = parent / 'checkpoint.json'
    pinned = hashlib.sha256(cp.read_bytes()).hexdigest()
    _, proc, result = run(tmp_path, 'clean', cp, pinned, 'resumed')
    assert proc.returncode != 0 and not result['admissible']


def test_missing_truncated_and_modified_artifacts_fail_closed(tmp_path):
    directory, process, _ = run(tmp_path, 'warning')
    journal = directory / 'warnings.jsonl'
    journal.write_bytes(journal.read_bytes().rstrip(b'\n'))
    result = evaluate(directory, CONTRACT, process.returncode, process.stdout, process.stderr)
    assert 'capture_incomplete' in result['reasons']
    (directory / 'receipt.json').unlink()
    assert not evaluate(directory, CONTRACT, 0, b'', b'')['admissible']
