"""Harness-independent, revision-checked research journal and derived summaries."""

from __future__ import annotations

import json
import os
import re
import socket
from contextlib import contextmanager
from datetime import datetime, timezone

import yaml

STATUSES = {
    "stage": {"planned", "ready", "active", "blocked", "complete"},
    "task": {"planned", "ready", "active", "blocked", "done", "cancelled"},
    "decision": {"proposed", "accepted", "rejected", "superseded"},
    "check": {"planned", "passed", "failed", "inconclusive"},
    "result": {"preliminary", "validated", "rejected", "superseded"},
    "issue": {"open", "investigating", "fixed", "verified", "dismissed"},
    "hypothesis": {"proposed", "testing", "supported", "contradicted", "inconclusive"},
    "plan": {"proposed", "active", "completed", "superseded"},
}
REQUIRED_EVIDENCE = {
    ("stage", "complete"), ("task", "done"), ("check", "passed"),
    ("check", "failed"), ("result", "validated"), ("issue", "fixed"),
    ("issue", "verified"), ("hypothesis", "supported"), ("hypothesis", "contradicted"),
}
KINDS_RU = {
    "task": "Задачи", "decision": "Решения", "check": "Проверки",
    "result": "Результаты", "issue": "Ошибки и проблемы",
    "hypothesis": "Гипотезы и альтернативы", "plan": "Дальнейшие планы",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_project(root):
    return yaml.safe_load((root / "tracking/project.yaml").read_text(encoding="utf-8"))


def stage_dir(root, stage):
    if stage not in read_project(root)["stages"]:
        raise ValueError(f"Unknown stage: {stage}")
    return root / "tracking/stages" / stage


def read_events(root, stage):
    path = stage_dir(root, stage) / "journal.jsonl"
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        event = json.loads(line)
        if event["revision"] != number or event["stage"] != stage:
            raise ValueError(f"Invalid journal sequence: {path}:{number}")
        events.append(event)
    return events


def current_records(events):
    records = {}
    for event in events:
        record = event["record"]
        records.pop(record["id"], None)
        records[record["id"]] = record
    return records


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextmanager
def writer_lock(root):
    path = root / "tracking/.write.lock"
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as exc:
        raise ValueError("Tracking is locked; retry after the other writer finishes. "
                         "Do not remove a live lock.") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "host": socket.gethostname(), "created_utc": utc_now()}, stream)
        yield
    finally:
        path.unlink(missing_ok=True)


def validate_record(root, stage, record, old=None):
    required = {"id", "kind", "title", "status", "summary", "refs", "next_action", "owner"}
    optional = {"detail_path", "related_ids", "supersedes", "affected_runs"}
    if not isinstance(record, dict) or not required <= record.keys():
        raise ValueError(f"Record requires fields: {sorted(required)}")
    if record.keys() - required - optional:
        raise ValueError("Unknown record fields")
    if not re.fullmatch(re.escape(stage) + r"-[A-Z][A-Z0-9_-]*", record["id"]):
        raise ValueError("ID must start with the stage and a stable uppercase code")
    kind, status = record["kind"], record["status"]
    if kind not in STATUSES or status not in STATUSES[kind]:
        raise ValueError(f"Invalid kind/status: {kind}/{status}")
    if kind == "stage" and record["id"] != stage + "-STAGE":
        raise ValueError("The stage record must use <stage>-STAGE")
    for name, maximum in [("title", 160), ("summary", 1200), ("next_action", 500), ("owner", 100)]:
        value = record[name]
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{name}: nonempty text, at most {maximum} characters")
    if not isinstance(record["refs"], list) or not all(isinstance(x, str) for x in record["refs"]):
        raise ValueError("refs must be a list of local evidence paths")
    if (kind, status) in REQUIRED_EVIDENCE and not record["refs"]:
        raise ValueError("This status requires evidence refs")
    paths = record["refs"] + ([record["detail_path"]] if record.get("detail_path") else [])
    for value in paths:
        path = (root / value.split("#", 1)[0]).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Evidence path must be an existing file inside the project: {value}")
    for key in ("related_ids", "affected_runs"):
        if key in record and (not isinstance(record[key], list)
                              or not all(isinstance(x, str) for x in record[key])):
            raise ValueError(f"{key} must be a list of strings")
    if old and old["kind"] != kind:
        raise ValueError("Cannot change the kind of an existing record")
    if old and old["kind"] == "task" and old["status"] == "active":
        if record["owner"] != old["owner"]:
            raise ValueError("Release the active task before transferring ownership")


def add_record(root, stage, record, actor, expected_revision):
    if not actor.strip() or len(actor) > 100:
        raise ValueError("actor must be a short nonempty identifier")
    with writer_lock(root):
        events = read_events(root, stage)
        if len(events) != expected_revision:
            raise ValueError(f"Revision conflict: expected {expected_revision}, actual {len(events)}; "
                             "reload the stage and reconcile before retrying")
        if not isinstance(record, dict):
            raise ValueError("Record must be a JSON object")
        old = current_records(events).get(record.get("id"))
        validate_record(root, stage, record, old)
        if old and old["kind"] == "task" and old["status"] == "active" and old["owner"] != actor:
            raise ValueError("Active task is owned by another actor; coordinate a release first")
        if record["kind"] == "task" and record["status"] == "active" and record["owner"] != actor:
            raise ValueError("An active task must be owned by the writing actor")
        event = {"schema_version": "1.0", "stage": stage, "revision": len(events) + 1,
                 "utc": utc_now(), "actor": actor, "record": record}
        path = stage_dir(root, stage) / "journal.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rewrite atomically while preserving every previous event byte for byte.
        previous = path.read_text(encoding="utf-8") if path.exists() else ""
        atomic_text(path, previous + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        render_unlocked(root)
        return event


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_unlocked(root):
    project = read_project(root)
    index = {"schema_version": "1.0", "stages": {}}
    decisions = ["# Решения проекта", "", "Производный индекс; не редактировать вручную. "
                 "Основания исходных решений: [bootstrap_decisions.md](docs/bootstrap_decisions.md).", "",
                 "| ID | Этап | Статус | Решение | Основание |", "|---|---|---|---|---|"]
    plan = ["# План проекта", "", "Производная сводка журналов. Проценты не вычисляются: "
            "завершение определяется проверенными продуктами и воротами.", "",
            "| Этап | Статус | Цель | Зависимости | Следующий шаг |", "|---|---|---|---|---|"]
    for stage, meta in project["stages"].items():
        events = read_events(root, stage)
        records = current_records(events)
        state = records.get(stage + "-STAGE", {"status": "planned", "summary": "Ещё не начат",
                                               "next_action": meta["next_action"]})
        index["stages"][stage] = {"revision": len(events), "status": state["status"],
                                    "records": list(records.values())}
        folder = stage_dir(root, stage)
        head = [f"# {stage} — {meta['title']}", "", "Производная сводка; источник — journal.jsonl.", "",
                f"Revision: {len(events)}. Статус: **{state['status']}**.", "",
                f"Состояние: {state['summary']}", "", f"Следующий шаг: {state['next_action']}", "",
                f"Зависимости: {', '.join(meta['depends_on']) or 'нет'}.", "",
                f"Критерий этапа: {meta['gate']}", "",
                "[Полный индекс записей](RECORDS.md) · [История](journal.jsonl)"]
        if (root / f"docs/stages/{stage}.md").is_file():
            head += ["", f"[Полное досье этапа](../../../docs/stages/{stage}.md) · "
                     "[Общая картина исследования](../../../docs/context/project_overview.md)"]
        full = [f"# Все записи {stage}", "", f"Revision: {len(events)}."]
        for kind, label in KINDS_RU.items():
            rows = [r for r in records.values() if r["kind"] == kind]
            if not rows:
                continue
            # SUMMARY is bounded; full index and event history retain everything.
            head += ["", f"## {label}", "", f"Всего: {len(rows)}. Последние/действующие записи:", ""]
            priority = {"active", "blocked", "open", "investigating", "failed", "testing", "proposed"}
            recent = list(reversed(rows))
            recent.sort(key=lambda r: r["status"] not in priority)
            for r in recent[:3]:
                head += [f"- `{r['id']}` [{r['status']}] {r['title']}. {r['summary'][:180]}"]
            full += ["", f"## {label}", "", "| ID | Статус | Запись | Следующий шаг | Детали/доказательства |",
                     "|---|---|---|---|---|"]
            for r in rows:
                links = ", ".join(f"[{p}](../../../{p})" for p in r["refs"])
                full.append(f"| {r['id']} | {r['status']} | {cell(r['title'])}: "
                            f"{cell(r['summary'])} | {cell(r['next_action'])} | {links} |")
                if kind == "decision":
                    decisions.append(f"| {r['id']} | {stage} | {r['status']} | "
                                     f"{cell(r['title'])} | [Запись](tracking/stages/{stage}/RECORDS.md) |")
        atomic_text(folder / "SUMMARY.md", "\n".join(head) + "\n")
        atomic_text(folder / "RECORDS.md", "\n".join(full) + "\n")
        plan.append(f"| [{stage}](tracking/stages/{stage}/SUMMARY.md) | {state['status']} | "
                    f"{cell(meta['title'])} | {', '.join(meta['depends_on']) or '—'} | "
                    f"{cell(state['next_action'])} |")
    focus = project["focus_stage"]
    focus_state = index["stages"][focus]
    stage_state = next((r for r in focus_state["records"] if r["kind"] == "stage"), None)
    state_lines = ["# Состояние исследования", "", "Производная точка входа. Не редактировать вручную; "
                   "обновлять журналы через `scripts/project.py track add`, затем сводки восстанавливаются автоматически.", "",
                   f"Текущий фокус: **{focus} — {project['stages'][focus]['title']}**.", "",
                   stage_state["summary"] if stage_state else "Этап ещё не начат.", "",
                   "Следующий шаг: " + (stage_state["next_action"] if stage_state else
                                         project["stages"][focus]["next_action"]), "",
                   "| Этап | Статус | Revision |", "|---|---|---|"]
    for stage, value in index["stages"].items():
        state_lines.append(f"| [{stage}](tracking/stages/{stage}/SUMMARY.md) | "
                           f"{value['status']} | {value['revision']} |")
    state_lines += ["", "Читать сначала эту сводку, затем SUMMARY текущего этапа. "
                    "Подробные записи и исходы загружать по ID и ссылкам. "
                    "[Протокол трекинга](docs/tracking_protocol.md) · [План](PLAN.md) · "
                    "[Решения](DECISIONS.md).", "",
                    "Активные исполнения проверять по `tracking/runtime.json`; файл является "
                    "реестром, не монитором процессов. Завершение диалога не создаёт фоновую службу."]
    if (root / "docs/context/project_overview.md").is_file():
        state_lines += ["", "Научный контекст: [общая картина](docs/context/project_overview.md), "
                        f"[досье текущего этапа](docs/stages/{focus}.md). "
                        "Досье содержит полный раздел программы и порядок работ; "
                        "для S0 вместо досье используются инфраструктурные протоколы."] if focus != "S0" else []
    atomic_text(root / "STATE.md", "\n".join(state_lines) + "\n")
    atomic_text(root / "PLAN.md", "\n".join(plan) + "\n")
    atomic_text(root / "DECISIONS.md", "\n".join(decisions) + "\n")
    atomic_text(root / "tracking/index.json", json.dumps(index, ensure_ascii=False, indent=2) + "\n")


def render(root):
    with writer_lock(root):
        render_unlocked(root)
