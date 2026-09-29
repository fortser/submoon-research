import json
import shutil
from pathlib import Path

import pytest

from submoon_research.workflows.smoke import run_smoke, sha256, validate_smoke_config


def test_numerical_reference_formats_and_immutable_runs(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "configs/experiments").mkdir(parents=True)
    for path in ["configs/resources.yaml", "configs/experiments/L0_smoke.yaml"]:
        shutil.copyfile(root / path, tmp_path / path)
    first, passed = run_smoke(tmp_path)
    assert passed
    before = sha256(first / "manifest.json")
    second, passed = run_smoke(tmp_path)
    assert passed and first != second
    assert sha256(first / "manifest.json") == before
    manifest = json.loads((first / "manifest.json").read_text())
    assert manifest["data_kind"] == "synthetic"
    assert manifest["status"] == "completed"
    for path, digest in manifest["artifacts_sha256"].items():
        assert sha256(first / path) == digest
    other = json.loads((second / "manifest.json").read_text())
    assert manifest["scientific_id"] == other["scientific_id"]


def test_draft_cannot_be_run_as_smoke():
    with pytest.raises(ValueError):
        validate_smoke_config({"status": "draft", "physics_level": "L1"})
