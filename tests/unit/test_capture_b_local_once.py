"""W2-D037: узкий opt-in, прежний общий лимит и независимые остановки."""
import json
import sys

import pytest

from submoon_research.diagnostics.ias15_replay import StepTrace
from test_p2_file_contracts import load


def test_profile_is_exact_and_default_limit_unchanged():
    launcher = load('capture_b_local_once')
    diag = load('diag_b_failure_p2_2')
    args = diag.parse(launcher.arguments())
    with pytest.raises(ValueError, match='600'):
        diag.validate_budget(args)
    assert diag.validate_budget(args, 'W2-D037') == 5400
    with pytest.raises(ValueError, match='W2-D037'):
        diag.validate_budget(args, 'other')
    for field, bad in [('orbit', 'other'), ('years', 10000.), ('ring', 128), ('wall_seconds', 5401.),
                       ('output', 'other.json'), ('snapshot_at', 0.), ('small_dt', .001),
                       ('source_jobs_sha256', '0'*64), ('trace_after', 0.), ('max_steps', 100)]:
        changed = diag.parse(launcher.arguments())
        setattr(changed, field, bad)
        with pytest.raises(ValueError, match='W2-D037'):
            diag.validate_budget(changed, 'W2-D037')


def test_trace_long_budget_requires_explicit_limit(tmp_path):
    kwargs = dict(directory=tmp_path, figures=[], identity={})
    with pytest.raises(ValueError, match='wall limit'):
        StepTrace(**kwargs, wall_seconds=601)
    assert StepTrace(**kwargs, wall_seconds=5370, wall_limit_seconds=5400).wall_seconds == 5370
    with pytest.raises(ValueError, match='wall limit'):
        StepTrace(**kwargs, wall_seconds=5401, wall_limit_seconds=5400)


@pytest.mark.skipif(sys.platform != 'linux', reason='WSL/Linux supervisor uses /proc')
@pytest.mark.parametrize('reason', ['wall_budget', 'operator_stop', 'memory_budget', 'output_budget', 'worker_exit'])
def test_supervisor_stops_without_retry(tmp_path, reason):
    launcher = load('capture_b_local_once')
    options = dict(wall_seconds=.3, poll_seconds=.02)
    code = 'import time; time.sleep(20)'
    if reason == 'operator_stop':
        (tmp_path/'STOP').touch()
    elif reason == 'memory_budget':
        options['memory_bytes'] = 1
    elif reason == 'output_budget':
        options['output_bytes'] = 1
    elif reason == 'worker_exit':
        code = 'raise SystemExit(7)'
        options['wall_seconds'] = 3
    assert launcher.supervise_worker([sys.executable, '-c', code], tmp_path, **options) == 0
    report = json.loads((tmp_path/'final.json').read_text())
    assert report['stop_reason'] == reason
    assert report['wall_seconds'] < 5
    if reason == 'worker_exit':
        assert report['returncode'] == 7
    else:
        assert report['returncode'] != 0
    assert json.loads((tmp_path/'heartbeat.json').read_text())['status'] == 'finished'


def test_launch_exclusive_marker_prevents_repeat(tmp_path, monkeypatch):
    launcher = load('capture_b_local_once')
    attempt = tmp_path/'attempt'
    attempt.mkdir()
    (tmp_path/'preflight.json').write_text(json.dumps(dict(code_sha256={}, python=sys.executable)))
    monkeypatch.setattr(launcher, 'BASE', tmp_path)
    monkeypatch.setattr(launcher, 'ATTEMPT', attempt)
    monkeypatch.setattr(launcher, 'code_hashes', lambda: {})
    monkeypatch.setattr(launcher.shutil, 'which', lambda _: '/usr/bin/timeout')
    monkeypatch.setattr(launcher.shutil, 'disk_usage', lambda _: type('Disk', (), {'free': 10*1024**3})())
    def forbidden(*args, **kwargs):
        raise AssertionError('Не должен запускать процесс')
    monkeypatch.setattr(launcher.subprocess, 'Popen', forbidden)
    with pytest.raises(FileExistsError):
        launcher.launch()
