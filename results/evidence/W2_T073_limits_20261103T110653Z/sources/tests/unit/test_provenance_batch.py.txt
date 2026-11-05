"""Контракт начального снимка кода: реальные записи в синтетический каталог."""
import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import yaml

import submoon_research.execution as execution


@pytest.fixture
def synthetic_package(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    package = root / 'src' / 'submoon_research'
    package.mkdir(parents=True)
    (root / 'configs').mkdir()
    (root / 'configs' / 'resources.yaml').write_text(yaml.safe_dump({
        'pilot_wall_seconds': 600, 'pilot_output_mib': 256,
        'minimum_free_disk_gib': 0, 'threads_per_worker': 1,
    }), encoding='utf-8')
    (package / 'execution.py').write_bytes(b'# synthetic execution\n')
    (package / 'module.py').write_bytes(b'VALUE = 12\n')
    (package / 'contract.json').write_bytes(b'{"synthetic":true}\n')
    (package / 'ignore.txt').write_bytes(b'not part of snapshot')
    monkeypatch.setattr(execution, '__file__', str(package / 'execution.py'))
    return root, package


def test_pending_precedes_copy_complete_has_exact_saved_bytes(synthetic_package, monkeypatch):
    root, package = synthetic_package
    expected = sorted(p.relative_to(root).as_posix() for p in package.iterdir()
                      if p.suffix in ('.py', '.json'))
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    original = Path.read_bytes
    seen = []

    def observe_read(path):
        if path.parent == package:
            manifest = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
            assert manifest['status'] == 'running'
            assert manifest['validation']['status'] == 'not_run'
            assert manifest['initial_code_snapshot'] == {
                'state': 'pending', 'expected_paths': expected, 'published_count': 0,
            }
            assert manifest['code_sha256'] == {}
            seen.append(path)
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', observe_read)
    with run:
        disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
        assert disk['schema_version'] == '0.3'
        assert disk['initial_code_snapshot'] == {
            'state': 'complete', 'expected_paths': expected, 'published_count': len(expected),
        }
        assert set(disk['code_sha256']) == set(expected)
        for name in expected:
            assert hashlib.sha256((run.folder / 'code' / name).read_bytes()).hexdigest() == disk['code_sha256'][name]
    assert len(seen) == len(expected)


@pytest.mark.parametrize('additional_files', [0, 8])
def test_initial_manifest_publications_are_constant(synthetic_package, monkeypatch, additional_files):
    root, package = synthetic_package
    for index in range(additional_files):
        (package / f'extra_{index}.py').write_text(f'VALUE = {index}\n', encoding='utf-8')
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    publish = execution.write_json
    calls = []

    def observe_write(path, data):
        if path == run.folder / 'manifest.json':
            calls.append(json.loads(json.dumps(data)))
        return publish(path, data)

    monkeypatch.setattr(execution, 'write_json', observe_write)
    with run:
        assert len(calls) == 2
        assert [entry['initial_code_snapshot']['state'] for entry in calls] == ['pending', 'complete']
        assert len(calls[-1]['code_sha256']) == 3 + additional_files


@pytest.mark.parametrize('failure_at', [1, 2])
def test_copy_error_fails_before_body(synthetic_package, monkeypatch, failure_at):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    original = run._copy_code
    calls = 0

    def fail_copy(path, name):
        nonlocal calls
        calls += 1
        if calls == failure_at:
            raise OSError('injected copy failure')
        return original(path, name)

    monkeypatch.setattr(run, '_copy_code', fail_copy)
    with pytest.raises(OSError, match='injected copy failure'):
        with run:
            pytest.fail('тело не должно запускаться')
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed'
    assert disk['validation']['status'] == 'not_run'
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert disk['initial_code_snapshot']['published_count'] == 0
    assert disk['code_sha256'] == {}


def test_short_copy_is_rejected(synthetic_package, monkeypatch):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    original = Path.write_bytes

    def short_write(path, data):
        if path.is_relative_to(run.folder / 'code'):
            original(path, data[:1])
            return 1
        return original(path, data)

    monkeypatch.setattr(Path, 'write_bytes', short_write)
    with pytest.raises(OSError, match='Неполная запись'):
        with run:
            pytest.fail('тело не должно запускаться')
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed' and disk['code_sha256'] == {}


def test_commit_and_finalization_failure_preserves_first_error(synthetic_package, monkeypatch):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    original = execution.write_json
    attempted = []

    def fail_publications(path, data):
        if path == run.folder / 'manifest.json':
            attempted.append(data['initial_code_snapshot']['state'])
            if len(attempted) > 1:
                raise OSError('commit failure' if len(attempted) == 2 else 'finalization failure')
        return original(path, data)

    monkeypatch.setattr(execution, 'write_json', fail_publications)
    with pytest.raises(OSError, match='commit failure') as caught:
        with run:
            pytest.fail('тело не должно запускаться')
    assert isinstance(caught.value.__cause__, OSError)
    assert 'finalization failure' in str(caught.value.__cause__)
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'running'
    assert disk['initial_code_snapshot']['state'] == 'pending'
    assert disk['code_sha256'] == {}


@pytest.mark.parametrize('failure', ['fsync', 'replace_before', 'replace_after'])
@pytest.mark.parametrize('finalization_fails', [False, True])
def test_real_publication_faults_preserve_safe_state(synthetic_package, monkeypatch,
                                                     failure, finalization_fails):
    """Инъекция на реальных системных вызовах внутри atomic_text, не в write_json."""
    root, _ = synthetic_package
    run = execution.RunContext(root, 'fault', output_mib=1)
    original_write = execution.write_json
    original_fsync = os.fsync
    original_replace = os.replace
    phase = {'commit': False, 'triggered': False, 'second': False}
    observed = []

    def observe_write(path, data):
        committing = (path == run.folder / 'manifest.json'
                      and data.get('initial_code_snapshot', {}).get('state') == 'complete')
        phase['commit'] = committing
        try:
            return original_write(path, data)
        finally:
            phase['commit'] = False

    def fault_fsync(fd):
        if phase['commit'] and failure == 'fsync' and not phase['triggered']:
            phase['triggered'] = True
            observed.append('commit-fsync')
            raise OSError('injected commit fsync')
        if phase['triggered'] and finalization_fails and not phase['second']:
            phase['second'] = True
            observed.append('finalization-fsync')
            raise OSError('injected finalization fsync')
        return original_fsync(fd)

    def fault_replace(src, dst):
        target = Path(dst) == run.folder / 'manifest.json'
        if phase['commit'] and target and failure.startswith('replace') and not phase['triggered']:
            phase['triggered'] = True
            observed.append(failure)
            if failure == 'replace_after':
                original_replace(src, dst)
            raise OSError('injected commit replace')
        return original_replace(src, dst)

    monkeypatch.setattr(execution, 'write_json', observe_write)
    monkeypatch.setattr(os, 'fsync', fault_fsync)
    monkeypatch.setattr(os, 'replace', fault_replace)
    with pytest.raises(OSError, match='injected commit') as caught:
        with run:
            pytest.fail('тело не должно запускаться')
    assert phase['triggered'] and bool(phase['second']) == finalization_fails
    assert len(observed) == 1 + int(finalization_fails)
    if finalization_fails:
        assert isinstance(caught.value.__cause__, OSError)
        assert 'finalization fsync' in str(caught.value.__cause__)
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['validation']['status'] == 'not_run'
    if not finalization_fails:
        assert disk['status'] == 'failed'
        assert disk['initial_code_snapshot']['state'] == 'failed'
        assert disk['code_sha256'] == {}
    elif failure == 'replace_after':
        # replace уже случился; не угадывать status, полный индекс остаётся на диске.
        assert disk['status'] == 'running'
        assert disk['initial_code_snapshot']['state'] == 'complete'
        assert len(disk['code_sha256']) == 3
        for name, digest in disk['code_sha256'].items():
            assert hashlib.sha256((run.folder / 'code' / name).read_bytes()).hexdigest() == digest
    else:
        assert disk['status'] == 'running'
        assert disk['initial_code_snapshot']['state'] == 'pending'
        assert disk['code_sha256'] == {}


@pytest.mark.parametrize('error,status', [(RuntimeError('injected'), 'failed'),
                                          (KeyboardInterrupt(), 'cancelled')])
def test_error_after_commit_keeps_snapshot_but_fails_run(synthetic_package, error, status):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    with pytest.raises(type(error)):
        with run:
            raise error
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == status
    assert disk['initial_code_snapshot']['state'] == 'complete'
    assert len(disk['code_sha256']) == 3
    assert disk['validation']['status'] == 'not_run'


@pytest.mark.parametrize('barrier', ['before_commit', 'after_commit'])
def test_owned_child_killed_at_snapshot_barrier(synthetic_package, barrier, tmp_path):
    root, package = synthetic_package
    receipt = tmp_path / 'receipt.json'
    child_script = r'''
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[5])
import submoon_research.execution as e
root, package, receipt, barrier = map(Path, sys.argv[1:5])
e.__file__ = str(package/'execution.py')
r = e.RunContext(root, 'kill-test', output_mib=1)
original_copy = r._copy_code
original_write = e.write_json
count = [0]
def wait_here():
    temporary = receipt.with_suffix('.tmp')
    temporary.write_text(json.dumps({'folder': str(r.folder)}), encoding='utf-8')
    temporary.replace(receipt)
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        time.sleep(.02)
    raise RuntimeError('barrier deadline')
def copy(path, name):
    result = original_copy(path, name)
    count[0] += 1
    if str(barrier) == 'before_commit' and count[0] == 1:
        wait_here()
    return result
def write(path, data):
    result = original_write(path, data)
    if (str(barrier) == 'after_commit' and path == r.folder/'manifest.json'
            and data.get('initial_code_snapshot', {}).get('state') == 'complete'):
        wait_here()
    return result
r._copy_code = copy
e.write_json = write
with r:
    (root/'body_started').write_text('unexpected', encoding='utf-8')
'''
    process = subprocess.Popen([sys.executable, '-c', child_script, str(root), str(package),
                                str(receipt), barrier, str(Path(__file__).resolve().parents[2] / 'src')], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 12
        while not receipt.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if not receipt.exists():
            if process.poll() is not None:
                _, stderr = process.communicate(timeout=5)
                pytest.fail(f'child не достиг barrier: {stderr.decode(errors="replace")}')
            pytest.fail('child не достиг barrier до deadline')
        folder = Path(json.loads(receipt.read_text(encoding='utf-8'))['folder'])
        process.kill()
        process.communicate(timeout=5)
        assert process.returncode != 0
        disk = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
        assert disk['status'] == 'running'
        assert disk['validation']['status'] == 'not_run'
        assert not (root / 'body_started').exists()
        if barrier == 'before_commit':
            assert disk['initial_code_snapshot']['state'] == 'pending'
            assert disk['code_sha256'] == {}
        else:
            assert disk['initial_code_snapshot']['state'] == 'complete'
            assert len(disk['code_sha256']) == 3
            for name, digest in disk['code_sha256'].items():
                assert hashlib.sha256((folder / 'code' / name).read_bytes()).hexdigest() == digest
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_later_save_code_is_immediately_published(synthetic_package):
    root, _ = synthetic_package
    extra = root / 'late.py'
    extra.write_bytes(b'# later file\n')
    run = execution.RunContext(root, 'synthetic', output_mib=1)
    with run:
        run.save_code(extra)
        disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
        assert disk['code_sha256']['late.py'] == hashlib.sha256(extra.read_bytes()).hexdigest()
        assert disk['initial_code_snapshot']['published_count'] == 3


@pytest.mark.parametrize('defer_disk_check', [False, True])
def test_snapshot_output_budget_rejects_before_commit(synthetic_package, monkeypatch, defer_disk_check):
    root, package = synthetic_package
    (package / '000_large.py').write_bytes(b'#' * (2**20 + 1))
    run = execution.RunContext(root, 'budget', output_mib=1)
    original = run._copy_code
    copied = []

    def copy(path, name):
        digest = original(path, name)
        copied.append(name)
        # Проверяем и обычный опрос, и обязательный force после всего staging.
        run.next_disk_check = float('inf') if defer_disk_check else 0
        disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
        assert disk['code_sha256'] == {}
        assert disk['initial_code_snapshot']['state'] == 'pending'
        return digest

    monkeypatch.setattr(run, '_copy_code', copy)
    with pytest.raises(RuntimeError, match='дисковый бюджет'):
        with run:
            pytest.fail('тело не должно запускаться при превышении бюджета')
    assert len(copied) == (4 if defer_disk_check else 1)
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed'
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert disk['initial_code_snapshot']['published_count'] == 0
    assert disk['code_sha256'] == {}
    assert not run._initial_code_committed


def test_wall_exhaustion_during_copy_rejects_before_commit(synthetic_package, monkeypatch):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'wall', output_mib=1)
    original = run._copy_code
    copied = []

    def copy(path, name):
        digest = original(path, name)
        copied.append(name)
        # Детерминированная имитация истёкшего времени, без долгого sleep.
        run.started_wall = execution.time.perf_counter() - run.wall_seconds - 1
        return digest

    monkeypatch.setattr(run, '_copy_code', copy)
    with pytest.raises(TimeoutError, match='wall budget'):
        with run:
            pytest.fail('тело не должно запускаться')
    assert len(copied) == 1
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed' and disk['code_sha256'] == {}
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert not run._initial_code_committed


@pytest.mark.parametrize('options', [dict(wall_seconds=601, output_mib=1),
                                     dict(wall_seconds=120, output_mib=257)])
def test_initial_global_limits_do_not_allow_copy(synthetic_package, options):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'limits', **options)
    with pytest.raises(ValueError, match='выше глобального'):
        with run:
            pytest.fail('тело не должно запускаться')
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed'
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert disk['code_sha256'] == {}
    assert not (run.folder / 'code').exists()


@pytest.mark.parametrize('failure_at', [1, 2])
@pytest.mark.parametrize('interrupt', [False, True])
def test_read_failure_or_interrupt_during_snapshot(synthetic_package, monkeypatch, failure_at, interrupt):
    root, package = synthetic_package
    run = execution.RunContext(root, 'read-failure', output_mib=1)
    original = Path.read_bytes
    reads = []
    error = KeyboardInterrupt('injected read interrupt') if interrupt else OSError('injected source read')

    def read(path):
        if path.parent == package:
            reads.append(path)
            if len(reads) == failure_at:
                raise error
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', read)
    with pytest.raises(type(error)) as caught:
        with run:
            pytest.fail('тело не должно запускаться')
    assert caught.value is error
    assert len(reads) == failure_at
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == ('cancelled' if interrupt else 'failed')
    assert disk['validation']['status'] == 'not_run'
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert disk['code_sha256'] == {}


def test_enospc_during_copy_preserves_partial_run_and_foreign_files(synthetic_package, monkeypatch):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'no-space', output_mib=1)
    foreign = root / 'runs/foreign/manifest.json'
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b'{"foreign":true}')
    temporary = foreign.parent / '.manifest.json.foreign.tmp'
    temporary.write_bytes(b'foreign temporary file')
    original = Path.write_bytes
    copies = []

    def write(path, data):
        if path.is_relative_to(run.folder / 'code'):
            copies.append(path)
            if len(copies) == 2:
                original(path, data[:1])
                raise OSError(errno.ENOSPC, 'injected disk full')
        return original(path, data)

    monkeypatch.setattr(Path, 'write_bytes', write)
    with pytest.raises(OSError) as caught:
        with run:
            pytest.fail('тело не должно запускаться')
    assert caught.value.errno == errno.ENOSPC
    assert len(copies) == 2 and copies[0].is_file() and copies[1].stat().st_size == 1
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'failed' and disk['code_sha256'] == {}
    assert disk['initial_code_snapshot']['state'] == 'failed'
    assert foreign.read_bytes() == b'{"foreign":true}'
    assert temporary.read_bytes() == b'foreign temporary file'


def test_existing_run_directory_is_never_overwritten(synthetic_package):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'collision', output_mib=1)
    run.folder.mkdir(parents=True)
    (run.folder / 'manifest.json').write_bytes(b'{"existing":true}')
    (run.folder / '.foreign.tmp').write_bytes(b'untouched')
    before = {p: p.read_bytes() for p in run.folder.iterdir()}
    with pytest.raises(FileExistsError):
        with run:
            pytest.fail('тело не должно запускаться')
    assert {p: p.read_bytes() for p in run.folder.iterdir()} == before


def test_duplicate_snapshot_names_rejected_before_copy(synthetic_package, monkeypatch):
    root, _ = synthetic_package
    run = execution.RunContext(root, 'name-collision', output_mib=1)
    monkeypatch.setattr(run, '_code_name', lambda path: 'duplicate.py')
    with pytest.raises(ValueError, match='Коллизия путей'):
        with run:
            pytest.fail('тело не должно запускаться')
    assert not (run.folder / 'code').exists()
    assert not (run.folder / 'manifest.json').exists()
    assert not run._initial_code_committed


def test_installed_package_outside_workspace_keeps_canonical_names(synthetic_package, monkeypatch):
    root, package = synthetic_package
    installed = package.rename(root.parent / 'installed-package')
    monkeypatch.setattr(execution, '__file__', str(installed / 'execution.py'))
    run = execution.RunContext(root, 'installed', output_mib=1)
    with run:
        names = ['src/submoon_research/' + p.name for p in sorted(installed.iterdir())
                 if p.suffix in ('.py', '.json')]
        assert run.manifest['initial_code_snapshot']['expected_paths'] == names
        assert set(run.manifest['code_sha256']) == set(names)
        for name, digest in run.manifest['code_sha256'].items():
            assert hashlib.sha256((run.folder / 'code' / name).read_bytes()).hexdigest() == digest


def test_final_provenance_and_scientific_id_reconstruct_from_saved_bytes(synthetic_package):
    root, _ = synthetic_package
    source = root / 'input.json'
    source.write_bytes(b'{"synthetic":true}')
    run = execution.RunContext(root, 'identity', dict(data_kind='synthetic'), output_mib=1)
    with run:
        run.ledger.read('input.json', registered=True)
        run.validate({'synthetic_contract': True}, scope='synthetic_not_science')
    disk = json.loads((run.folder / 'manifest.json').read_text(encoding='utf-8'))
    assert disk['status'] == 'completed' and disk['validation']['status'] == 'passed'
    assert disk['initial_code_snapshot']['state'] == 'complete'
    assert disk['inputs_sha256']['input.json'] == hashlib.sha256(source.read_bytes()).hexdigest()
    for path, digest in disk['artifacts_sha256'].items():
        assert hashlib.sha256((run.folder / path).read_bytes()).hexdigest() == digest
    identity = dict(config_sha256=hashlib.sha256((run.folder / 'config.resolved.yaml').read_bytes()).hexdigest(),
                    inputs=disk['inputs_sha256'], code=disk['code_sha256'], environment=disk['environment'])
    expected = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                         allow_nan=False).encode('utf-8')).hexdigest()
    assert disk['scientific_id'] == expected
    assert {'git_commit', 'git_dirty', 'finished_utc'} <= disk.keys()
    before = (run.folder / 'manifest.json').read_bytes()
    with pytest.raises(FileExistsError):
        with run:
            pytest.fail('завершённый run нельзя открыть повторно')
    assert (run.folder / 'manifest.json').read_bytes() == before
