import json

import pytest
import yaml

from submoon_research.tracking import (
    add_record, current_records, read_events, render, writer_lock,
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tracking").mkdir()
    (tmp_path / "tracking/project.yaml").write_text(yaml.safe_dump({
        "focus_stage": "W0", "stages": {"W0": {"title": "Audit", "depends_on": [],
        "gate": "Audited data", "next_action": "Read source"}}}), encoding="utf-8")
    (tmp_path / "evidence.md").write_text("Measured evidence", encoding="utf-8")
    return tmp_path


def record(status="ready", owner="agent-a"):
    return {"id": "W0-T001", "kind": "task", "title": "Audit", "status": status,
            "summary": "Read the original source", "refs": ["evidence.md"],
            "next_action": "Check table", "owner": owner}


def test_history_retained_and_summaries_recover(project):
    add_record(project, "W0", record(), "agent-a", 0)
    first = (project / "tracking/stages/W0/journal.jsonl").read_bytes()
    add_record(project, "W0", record("active"), "agent-a", 1)
    assert (project / "tracking/stages/W0/journal.jsonl").read_bytes().startswith(first)
    assert len(read_events(project, "W0")) == 2
    assert current_records(read_events(project, "W0"))["W0-T001"]["status"] == "active"
    (project / "STATE.md").unlink()
    render(project)
    assert (project / "STATE.md").is_file()
    assert "active" in (project / "tracking/stages/W0/SUMMARY.md").read_text(encoding="utf-8")


def test_stale_writer_cannot_lose_update(project):
    add_record(project, "W0", record(), "agent-a", 0)
    before = (project / "tracking/stages/W0/journal.jsonl").read_bytes()
    with pytest.raises(ValueError, match="Revision conflict"):
        add_record(project, "W0", record("done"), "agent-b", 0)
    assert (project / "tracking/stages/W0/journal.jsonl").read_bytes() == before


def test_concurrent_writer_rejected(project):
    with writer_lock(project):
        with pytest.raises(ValueError, match="locked"):
            add_record(project, "W0", record(), "agent-a", 0)
    assert not (project / "tracking/.write.lock").exists()


def test_owner_cannot_be_silently_replaced(project):
    add_record(project, "W0", record("active"), "agent-a", 0)
    with pytest.raises(ValueError, match="another actor"):
        add_record(project, "W0", record("done"), "agent-b", 1)
    add_record(project, "W0", record("ready"), "agent-a", 1)
    add_record(project, "W0", record("active", "agent-b"), "agent-b", 2)


@pytest.mark.parametrize("refs", [[], ["missing.md"], ["../outside.md"]])
def test_completion_requires_local_evidence(project, refs):
    value = record("done")
    value["refs"] = refs
    with pytest.raises(ValueError):
        add_record(project, "W0", value, "agent-a", 0)
    assert read_events(project, "W0") == []


def test_corrupted_sequence_is_not_silently_repaired(project):
    add_record(project, "W0", record(), "agent-a", 0)
    path = project / "tracking/stages/W0/journal.jsonl"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["revision"] = 5
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sequence"):
        read_events(project, "W0")


def test_negative_result_is_retained(project):
    value = record()
    value.update(id="W0-H001", kind="hypothesis", status="contradicted")
    add_record(project, "W0", value, "agent-a", 0)
    assert "contradicted" in (project / "tracking/stages/W0/RECORDS.md").read_text(encoding="utf-8")
