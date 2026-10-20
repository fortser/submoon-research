import json

import pytest
import yaml

from submoon_research import tracking
from submoon_research.tracking import (
    add_record, current_records, read_events, render, writer_lock,
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "tracking").mkdir()
    (tmp_path / "tracking/project.yaml").write_text(yaml.safe_dump({
        "focus_stage": "W0", "stages": {"W0": {"title": "Audit", "depends_on": [],
        "gate": "Audited data"}}}), encoding="utf-8")
    (tmp_path / "evidence.md").write_text("Measured evidence", encoding="utf-8")
    return tmp_path


def record(status="ready", owner="agent-a", rid="W0-T001", kind="task", title="Audit"):
    return {"id": rid, "kind": kind, "title": title, "status": status,
            "summary": "Read the original source", "refs": ["evidence.md"],
            "next_action": f"Next for {rid}", "owner": owner}


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


def test_leftover_lock_file_does_not_block_journal(project):
    # Песочница, которая не может удалять файлы, раньше оставляла .write.lock навсегда.
    (project / "tracking/.write.lock").write_text('{"pid": 1, "host": "x"}', encoding="utf-8")
    event = add_record(project, "W0", record(), "agent-a", 0)
    assert event["revision"] == 1
    assert len(read_events(project, "W0")) == 1


def test_retry_with_same_input_is_idempotent(project):
    first = add_record(project, "W0", record(), "agent-a", 0)
    again = add_record(project, "W0", record(), "agent-a", 0)
    assert again["event_id"] == first["event_id"]
    assert len(read_events(project, "W0")) == 1
    with pytest.raises(ValueError, match="Revision conflict"):
        add_record(project, "W0", record("blocked"), "agent-a", 0)


def test_lost_write_is_reported(project, monkeypatch):
    add_record(project, "W0", record(), "agent-a", 0)
    real = tracking._write_journal

    def concurrent_overwrite(path, previous, event):
        foreign = dict(event, event_id="foreign-writer", actor="agent-b")
        real(path, previous, foreign)

    monkeypatch.setattr(tracking, "_write_journal", concurrent_overwrite)
    with pytest.raises(RuntimeError, match="потеряна"):
        add_record(project, "W0", record("active"), "agent-a", 1)


def test_monitor_lock_still_exclusive(project):
    with writer_lock(project):
        with pytest.raises(ValueError, match="locked"):
            with writer_lock(project):
                pass
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


def test_next_step_follows_newer_active_work(project):
    stage = record("active", rid="W0-STAGE", kind="stage", title="Audit stage")
    stage["next_action"] = "Old stage step"
    add_record(project, "W0", stage, "agent-a", 0)
    add_record(project, "W0", record("active"), "agent-a", 1)
    summary = (project / "tracking/stages/W0/SUMMARY.md").read_text(encoding="utf-8")
    state = (project / "STATE.md").read_text(encoding="utf-8")
    assert "Следующий шаг (W0-T001): Next for W0-T001" in summary
    assert "Где сейчас идёт работа" in state and "`W0-T001`" in state
    # Когда запись этапа обновлена позже, приоритет у неё.
    stage["next_action"] = "Fresh stage step"
    add_record(project, "W0", stage, "agent-a", 2)
    summary = (project / "tracking/stages/W0/SUMMARY.md").read_text(encoding="utf-8")
    assert "Следующий шаг (W0-STAGE): Fresh stage step" in summary


def test_stage_lag_is_flagged(project):
    add_record(project, "W0", record("active", rid="W0-STAGE", kind="stage", title="S"), "agent-a", 0)
    for n in range(tracking.STAGE_LAG_WARN + 1):
        add_record(project, "W0", record("passed", rid=f"W0-C{n:03d}", kind="check"), "agent-a", n + 1)
    summary = (project / "tracking/stages/W0/SUMMARY.md").read_text(encoding="utf-8")
    assert f"событий в журнале после неё: {tracking.STAGE_LAG_WARN + 1}" in summary


def test_summary_shows_current_decisions_and_open_failures(project):
    old = record("accepted", rid="W0-D001", kind="decision", title="Old rule")
    add_record(project, "W0", old, "agent-a", 0)
    new = record("accepted", rid="W0-D002", kind="decision", title="New rule")
    new["supersedes"] = "W0-D001"
    add_record(project, "W0", new, "agent-a", 1)
    add_record(project, "W0", dict(old, status="superseded"), "agent-a", 2)
    add_record(project, "W0", record("failed", rid="W0-C001", kind="check", title="Bad"), "agent-a", 3)
    fixed = record("passed", rid="W0-C002", kind="check", title="Replacement")
    fixed["supersedes"] = "W0-C001"
    add_record(project, "W0", fixed, "agent-a", 4)
    add_record(project, "W0", record("failed", rid="W0-C003", kind="check", title="Open"), "agent-a", 5)
    summary = (project / "tracking/stages/W0/SUMMARY.md").read_text(encoding="utf-8")
    decisions_part = summary.split("## Решения")[1].split("## ")[0]
    assert "[`W0-D002`](RECORDS.md#w0-d002) [accepted] New rule" in decisions_part
    assert "[`W0-D001`]" not in decisions_part
    assert "`W0-D001` (superseded)" in decisions_part
    assert "без замещающей проверки: `W0-C003`" in summary


def test_decision_links_use_anchors(project):
    add_record(project, "W0", record("accepted", rid="W0-D001", kind="decision"), "agent-a", 0)
    decisions = (project / "DECISIONS.md").read_text(encoding="utf-8")
    records = (project / "tracking/stages/W0/RECORDS.md").read_text(encoding="utf-8")
    assert "tracking/stages/W0/RECORDS.md#w0-d001" in decisions
    assert '<a id="w0-d001"></a>W0-D001' in records
