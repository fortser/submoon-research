"""Распаковка результатов аренды P2: существующие файлы сверяются по SHA-256, а не пропускаются.

Повод (P2-1, instance 55200007): download() пропускал существующие файлы, поэтому устаревший
локальный jobs.jsonl и пустой jobs.jsonl после оборванной распаковки не обновлялись.
"""
import importlib.util
import io
import tarfile
from pathlib import Path

import pytest


def load(name):
    p = Path(__file__).resolve().parents[2]/'scripts'/f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_archive(path, files):
    with tarfile.open(path, 'w:gz') as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_extract_replaces_stale_and_empty_files_and_keeps_old_copies(tmp_path):
    vast = load('vast_p2')
    root, work = tmp_path/'root', tmp_path/'root'/'scratch'/'p2_rental'
    run = root/'runs'/'W2-P2-1-7'
    run.mkdir(parents=True)
    (run/'jobs.jsonl').write_bytes(b'')                      # оборванная распаковка
    (run/'summary.json').write_bytes(b'{"old": true}')       # устаревший файл
    (run/'level_1y.json').write_bytes(b'same')               # совпадает с сервером
    archive = tmp_path/'results.tar.gz'
    make_archive(archive, {
        'runs/W2-P2-1-7/jobs.jsonl': b'{"a": 1}\n'*3,
        'runs/W2-P2-1-7/summary.json': b'{"new": true}',
        'runs/W2-P2-1-7/level_1y.json': b'same',
        'runs/W2-P2-1-7/progress.json': b'{}',
        'remote_logs/p2_status': b'done exit=0',
    })
    stats = vast._extract_results(archive, 7, root=root, work=work, stamp='S')
    assert stats['replaced'] == 2 and stats['unchanged'] == 1 and stats['new'] == 2 and stats['written'] == 4
    assert (run/'jobs.jsonl').read_bytes() == b'{"a": 1}\n'*3
    assert (run/'summary.json').read_bytes() == b'{"new": true}'
    assert (run/'progress.json').exists() and (work/'remote_logs_7'/'p2_status').exists()
    kept = work/'superseded_7_S'/'runs'/'W2-P2-1-7'
    assert (kept/'jobs.jsonl').read_bytes() == b'' and (kept/'summary.json').read_bytes() == b'{"old": true}'
    assert not list(root.rglob('*.partial'))
    # Повторная распаковка того же архива ничего не меняет.
    again = vast._extract_results(archive, 7, root=root, work=work, stamp='T')
    assert again['unchanged'] == 5 and again['written'] == 0 and not (work/'superseded_7_T').exists()


@pytest.mark.parametrize('name', ['runs/../evil.txt', 'other/file.txt', '/abs/file.txt'])
def test_extract_rejects_paths_outside_allowed_area(tmp_path, name):
    vast = load('vast_p2')
    archive = tmp_path/'bad.tar.gz'
    make_archive(archive, {name: b'x'})
    with pytest.raises(ValueError):
        vast._extract_results(archive, 7, root=tmp_path/'root', work=tmp_path/'work', stamp='S')
