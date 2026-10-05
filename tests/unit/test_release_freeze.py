import json
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest

from submoon_research.release import build_release,restore_release
from submoon_research.contracts.freeze import validate_freeze


def test_zip_path_traversal_rejected_before_writes(tmp_path):
    package=tmp_path/'bad.zip'
    with ZipFile(package,'w') as archive:
        archive.writestr('../escape','bad')
        archive.writestr('release.manifest.json',json.dumps(dict(files=[dict(path='../escape',sha256='0'*64,bytes=3)])))
    with pytest.raises(ValueError):
        restore_release(package,tmp_path/'restore')
    assert not (tmp_path/'restore').exists()


def test_draft_or_synthetic_does_not_freeze(tmp_path):
    with pytest.raises(ValueError):
        validate_freeze(tmp_path,dict(status='draft'))
    assert not list(Path(tmp_path).iterdir())


def test_release_preserves_unicode_and_spaces(tmp_path):
    root=tmp_path/'workspace'
    root.mkdir()
    subprocess.run(['git','init',str(root)],check=True,capture_output=True)
    name='Научная программа.md'
    (root/name).write_text('Первичный источник',encoding='utf-8')
    (root/'.env.example').write_text('TOKEN=',encoding='utf-8')
    (root/'.env.private').write_text('secret',encoding='utf-8')
    package=tmp_path/'release.zip'
    manifest=build_release(root,package)
    names={item['path'] for item in manifest['files']}
    assert name in names and '.env.example' in names
    assert '.env.private' not in names
    restored=tmp_path/'restored'
    restore_release(package,restored)
    assert (restored/name).read_bytes()==(root/name).read_bytes()
