import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from submoon_research.release import restore_release
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
