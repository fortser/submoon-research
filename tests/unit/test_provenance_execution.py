"""Происхождение, аварийная финализация и лимиты проверяются независимо от науки."""
import json
import hashlib
import sys
import subprocess

import pytest
import yaml

from submoon_research.execution import RunContext
from submoon_research.provenance import InputLedger


def root_fixture(tmp_path):
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/resources.yaml').write_text(yaml.safe_dump(dict(pilot_wall_seconds=600,
        pilot_output_mib=256, minimum_free_disk_gib=0, threads_per_worker=1)),encoding='utf-8')
    return tmp_path


def test_exact_bytes_are_used_and_unbound_or_changed_inputs_rejected(tmp_path):
    p=tmp_path/'input.json'
    original=b'{"value": 1}'
    p.write_bytes(original)
    ledger=InputLedger(tmp_path)
    with pytest.raises(ValueError,match='SHA'):
        ledger.json('input.json')
    ledger.bind('input.json',hashlib.sha256(original).hexdigest())
    assert ledger.json('input.json') == {'value':1}
    p.write_text('{"value": 2}')
    assert ledger.json('input.json') == {'value':1}
    fresh=InputLedger(tmp_path)
    fresh.bind('input.json',hashlib.sha256(original).hexdigest())
    with pytest.raises(ValueError,match='SHA'):
        fresh.json('input.json')
    with pytest.raises(ValueError):
        ledger.read('../escape')


@pytest.mark.parametrize('error,status',[(RuntimeError('injected'),'failed'),(KeyboardInterrupt(),'cancelled')])
def test_manifest_exists_before_work_and_failed_runs_are_finalized(tmp_path,error,status):
    root=root_fixture(tmp_path)
    run=RunContext(root,'test',output_mib=1)
    with pytest.raises(type(error)):
        with run:
            assert json.loads((run.folder/'manifest.json').read_text())['status']=='running'
            raise error
    manifest=json.loads((run.folder/'manifest.json').read_text())
    assert manifest['status']==status
    assert manifest['started_utc'] and manifest['finished_utc']
    assert manifest['validation']['status']=='not_run'
    assert manifest['error'] is not None


def test_overflow_and_external_timeout_are_failures(tmp_path):
    root=root_fixture(tmp_path)
    run=RunContext(root,'overflow',output_mib=1)
    with pytest.raises(RuntimeError):
        with run:
            (run.folder/'large').write_bytes(b'x'*2**20)
    assert run.manifest['status']=='failed'
    run=RunContext(root,'timeout',wall_seconds=5,output_mib=1)
    with pytest.raises(subprocess.TimeoutExpired):
        with run:
            run.subprocess([sys.executable,'-c','import time; time.sleep(2)'],timeout=0.1)
    assert run.manifest['status']=='failed'


def test_disk_reserve_checked_before_work(tmp_path,monkeypatch):
    from collections import namedtuple
    root=root_fixture(tmp_path)
    monkeypatch.setattr('submoon_research.execution.shutil.disk_usage',lambda root:namedtuple('Disk','total used free')(1,1,0))
    run=RunContext(root,'disk',output_mib=1)
    with pytest.raises(RuntimeError):
        with run:
            pytest.fail('Научная работа при отсутствии резерва')
    assert run.manifest['status']=='failed'
