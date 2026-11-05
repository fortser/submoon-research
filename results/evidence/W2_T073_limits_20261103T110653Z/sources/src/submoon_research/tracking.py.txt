"""Harness-independent, revision-checked research journal and derived summaries.

Since S0-T006 (organization audit 2026-10-13) the journal is written WITHOUT a file lock:
agents work sequentially, and the lock left stale files in sandboxes that cannot delete.
Protection that remains: expected_revision (no write from a stale view), the active-task
owner check, an idempotent event_id and a post-write check that the event is in place.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

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
STAGE_LAG_WARN = 10            # событий после последнего обновления <stage>-STAGE
IDLE_TASK_HOURS = 72           # active-задача без событий дольше этого — «возможно брошена»
SNIPPET = 120                  # символов summary в строке SUMMARY


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


def last_revisions(events):
    """ID записи -> revision её последнего события."""
    return {event["record"]["id"]: event["revision"] for event in events}


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Уникальное имя: два процесса не делят один временный файл.
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextmanager
def writer_lock(root):
    """Файловая блокировка ТОЛЬКО для фонового монитора рынка (tracking/runtime.json).

    Журнал и сводки ею больше не пользуются (S0-T006). Не применять там, где процесс
    не может удалять файлы: оставшийся lock блокирует следующего писателя.
    """
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


def owner_transfer_allowed():
    # Временный явный override: штатно владелец active-задачи должен сам её
    # освободить. При "1" разрешён перенос owner без участия прежнего владельца
    # (например, когда прежний harness недоступен). По умолчанию правило строгое.
    return os.environ.get("SUBMOON_ALLOW_OWNER_TRANSFER") == "1"


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
        if record["owner"] != old["owner"] and not owner_transfer_allowed():
            raise ValueError("Release the active task before transferring ownership")


def event_id_for(stage, expected_revision, actor, record):
    """Детерминированный ID события: повтор той же команды узнаётся и не создаёт дубля."""
    canon = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{stage}|{expected_revision}|{actor}|{canon}".encode("utf-8")).hexdigest()[:16]


def _write_journal(path, previous, event):
    # Переписываем атомарно, сохраняя все прежние события байт в байт.
    atomic_text(path, previous + json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")


def add_record(root, stage, record, actor, expected_revision):
    if not actor.strip() or len(actor) > 100:
        raise ValueError("actor must be a short nonempty identifier")
    if not isinstance(record, dict):
        raise ValueError("Record must be a JSON object")
    eid = event_id_for(stage, expected_revision, actor, record)
    events = read_events(root, stage)
    # Повтор после обрыва или ложной ошибки: событие уже записано — вернуть его.
    for event in events[expected_revision:]:
        if event.get("event_id") == eid:
            return event
    if len(events) != expected_revision:
        raise ValueError(f"Revision conflict: expected {expected_revision}, actual {len(events)}; "
                         "reload the stage and reconcile before retrying")
    old = current_records(events).get(record.get("id"))
    validate_record(root, stage, record, old)
    if (old and old["kind"] == "task" and old["status"] == "active"
            and old["owner"] != actor and not owner_transfer_allowed()):
        raise ValueError("Active task is owned by another actor; coordinate a release first")
    if record["kind"] == "task" and record["status"] == "active" and record["owner"] != actor:
        raise ValueError("An active task must be owned by the writing actor")
    event = {"schema_version": "1.0", "stage": stage, "revision": len(events) + 1,
             "utc": utc_now(), "actor": actor, "event_id": eid, "record": record}
    path = stage_dir(root, stage) / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else ""
    _write_journal(path, previous, event)
    # Проверка после записи вместо блокировки: событие должно стоять на своём месте.
    written = read_events(root, stage)
    if len(written) <= expected_revision or written[expected_revision].get("event_id") != eid:
        raise RuntimeError(
            f"Запись потеряна: журнал {stage} одновременно изменил другой писатель "
            f"(ожидалось событие {eid} на revision {expected_revision + 1}). "
            "Перечитайте этап (track show) и повторите с актуальным expected_revision.")
    try:
        render(root)
    except Exception as exc:  # noqa: BLE001 — событие уже записано; сводки восстановимы
        raise RuntimeError(
            f"Событие {record['id']} записано (revision {event['revision']}), но сводки не "
            f"обновлены: {exc}. Не повторяйте запись; выполните track render.") from exc
    return event


def cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def anchor(record_id):
    return record_id.lower()


def _parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stage_overview(stage, meta, events):
    """Сводные факты этапа для STATE/PLAN/SUMMARY (без файлового ввода-вывода)."""
    records = current_records(events)
    last = last_revisions(events)
    stage_id = stage + "-STAGE"
    state = records.get(stage_id, {"status": "planned", "summary": "Ещё не начат",
                                   "next_action": meta.get("next_action", "—")})
    stage_rev = last.get(stage_id, 0)
    active = sorted((r for r in records.values() if r["kind"] in ("task", "plan")
                     and r["status"] == "active"), key=lambda r: last[r["id"]], reverse=True)
    newest = active[0] if active else None
    if newest and last[newest["id"]] > stage_rev:
        next_step, next_source = newest["next_action"], newest["id"]
    else:
        next_step, next_source = state["next_action"], stage_id
    lag = len(events) - stage_rev if stage_id in records else 0
    idle = []
    if events:
        latest = _parse_utc(events[-1]["utc"])
        last_utc = {e["record"]["id"]: _parse_utc(e["utc"]) for e in events}
        idle = [r["id"] for r in records.values() if r["kind"] == "task" and r["status"] == "active"
                and latest - last_utc[r["id"]] > timedelta(hours=IDLE_TASK_HOURS)]
    return {"records": records, "last": last, "state": state, "stage_rev": stage_rev,
            "lag": lag, "active": active, "next_step": next_step, "next_source": next_source,
            "idle_tasks": idle,
            "active_tasks": [r["id"] for r in active if r["kind"] == "task"]}


def _item(r, prefix=""):
    return (f"- [`{r['id']}`]({prefix}RECORDS.md#{anchor(r['id'])}) [{r['status']}] "
            f"{r['title']}. {r['summary'][:SNIPPET].rstrip()}")


def _summary_sections(info):
    """SUMMARY по смыслу: действующее и незакрытое, а не «последнее тронутое»."""
    records, last = info["records"], info["last"]
    by_rev = sorted(records.values(), key=lambda r: last[r["id"]], reverse=True)
    superseded_targets = {r["supersedes"] for r in records.values() if r.get("supersedes")}
    rules = {
        "task": (lambda r: r["status"] in {"active", "blocked", "ready"}, 5,
                 "Активные, заблокированные и готовые к работе"),
        "decision": (lambda r: r["status"] in {"accepted", "proposed"}, 4,
                     "Действующие и предложенные, новые сверху"),
        "check": (lambda r: True, 3, "Последние проверки"),
        "result": (lambda r: True, 2, "Последние результаты"),
        "issue": (lambda r: r["status"] in {"open", "investigating"}, 5,
                  "Открытые и исследуемые"),
        "hypothesis": (lambda r: r["status"] in {"proposed", "testing"}, 3, "Проверяемые"),
        "plan": (lambda r: r["status"] in {"active", "proposed"}, 3, "Действующие и предложенные"),
    }
    lines = []
    for kind, label in KINDS_RU.items():
        rows = [r for r in by_rev if r["kind"] == kind]
        if not rows:
            continue
        keep, limit, caption = rules[kind]
        shown = [r for r in rows if keep(r)]
        lines += ["", f"## {label}", "", f"Всего: {len(rows)}. {caption}:", ""]
        lines += [_item(r) for r in shown[:limit]] or ["- нет"]
        if len(shown) > limit:
            rest = shown[limit:]
            lines.append(f"- … ещё {len(rest)}"
                         + (": " + ", ".join(f"`{r['id']}`" for r in rest)
                            if kind in {"task", "decision", "issue", "plan"} else " — в RECORDS.md"))
        if kind == "task" and info["idle_tasks"]:
            lines.append(f"- Возможно брошены (active без событий более {IDLE_TASK_HOURS} ч "
                         "до последнего события этапа): "
                         + ", ".join(f"`{x}`" for x in info["idle_tasks"]))
        if kind == "decision":
            other = [r for r in rows if r["status"] in {"superseded", "rejected"}]
            if other:
                lines.append("- Вытеснены или отклонены: "
                             + ", ".join(f"`{r['id']}` ({r['status']})" for r in other))
        if kind == "check":
            pending = [r for r in rows if r["status"] in {"failed", "inconclusive"}
                       and r["id"] not in superseded_targets]
            if pending:
                lines.append("- Провалы и неясные исходы без замещающей проверки: "
                             + ", ".join(f"`{r['id']}`" for r in pending))
        if kind == "issue":
            fixed = [r for r in rows if r["status"] == "fixed"]
            if fixed:
                lines.append("- Исправлены, ждут подтверждения (fixed): "
                             + ", ".join(f"`{r['id']}`" for r in fixed))
    return lines


def render_unlocked(root):
    """Совместимое имя: блокировки больше нет, см. render()."""
    render(root)


def render(root):
    project = read_project(root)
    index = {"schema_version": "1.0", "stages": {}}
    decisions = ["# Решения проекта", "", "Производный индекс; не редактировать вручную. "
                 "Основания исходных решений: [bootstrap_decisions.md](docs/bootstrap_decisions.md). "
                 "Одна запись целиком: `scripts/project.py track show --stage <этап> --id <ID>`.", "",
                 "| ID | Этап | Статус | Решение | Основание |", "|---|---|---|---|---|"]
    plan = ["# План проекта", "", "Производная сводка журналов. Проценты не вычисляются: "
            "завершение определяется проверенными продуктами и воротами. Следующий шаг берётся "
            "из самой свежей active-задачи или плана, если она новее записи этапа.", "",
            "| Этап | Статус | Цель | Зависимости | Следующий шаг |", "|---|---|---|---|---|"]
    overviews = {}
    for stage, meta in project["stages"].items():
        events = read_events(root, stage)
        info = stage_overview(stage, meta, events)
        overviews[stage] = info
        records, state = info["records"], info["state"]
        index["stages"][stage] = {"revision": len(events), "status": state["status"],
                                  "records": list(records.values())}
        folder = stage_dir(root, stage)
        stage_note = (f"Состояние (запись {stage}-STAGE, rev {info['stage_rev']}): {state['summary']}"
                      if info["stage_rev"] else f"Состояние: {state['summary']}")
        head = [f"# {stage} — {meta['title']}", "", "Производная сводка; источник — journal.jsonl.", "",
                f"Revision: {len(events)}. Статус: **{state['status']}**.", "", stage_note, ""]
        if info["lag"] > STAGE_LAG_WARN:
            head += [f"⚠ Запись {stage}-STAGE устарела — событий в журнале после неё: {info['lag']}. "
                     "Актуальное состояние — в активных задачах и планах ниже.", ""]
        head += [f"Следующий шаг ({info['next_source']}): {info['next_step']}", "",
                 f"Зависимости: {', '.join(meta['depends_on']) or 'нет'}.", "",
                 f"Критерий этапа: {meta['gate']}", "",
                 "[Полный индекс записей](RECORDS.md) · [История](journal.jsonl)"]
        if (root / f"docs/stages/{stage}.md").is_file():
            head += ["", f"[Полное досье этапа](../../../docs/stages/{stage}.md) · "
                     "[Общая картина исследования](../../../docs/context/project_overview.md)"]
        head += _summary_sections(info)
        full = [f"# Все записи {stage}", "", f"Revision: {len(events)}."]
        for kind, label in KINDS_RU.items():
            rows = [r for r in records.values() if r["kind"] == kind]
            if not rows:
                continue
            full += ["", f"## {label}", "", "| ID | Статус | Запись | Следующий шаг | Детали/доказательства |",
                     "|---|---|---|---|---|"]
            for r in rows:
                links = ", ".join(f"[{p}](../../../{p})" for p in r["refs"])
                full.append(f"| <a id=\"{anchor(r['id'])}\"></a>{r['id']} | {r['status']} | "
                            f"{cell(r['title'])}: {cell(r['summary'])} | {cell(r['next_action'])} | {links} |")
                if kind == "decision":
                    decisions.append(
                        f"| {r['id']} | {stage} | {r['status']} | {cell(r['title'])} | "
                        f"[Запись](tracking/stages/{stage}/RECORDS.md#{anchor(r['id'])}) |")
        atomic_text(folder / "SUMMARY.md", "\n".join(head) + "\n")
        atomic_text(folder / "RECORDS.md", "\n".join(full) + "\n")
        plan.append(f"| [{stage}](tracking/stages/{stage}/SUMMARY.md) | {state['status']} | "
                    f"{cell(meta['title'])} | {', '.join(meta['depends_on']) or '—'} | "
                    f"{cell(info['next_step'])} ({info['next_source']}) |")
    focus = project["focus_stage"]
    focus_info = overviews[focus]
    focus_rec = focus_info["records"].get(focus + "-STAGE")
    state_lines = ["# Состояние исследования", "", "Производная точка входа. Не редактировать вручную; "
                   "обновлять журналы через `scripts/project.py track add`, затем сводки восстанавливаются автоматически.", ""]
    working = [(s, i) for s, i in overviews.items() if i["active"]]
    if working:
        state_lines += ["## Где сейчас идёт работа", ""]
        for stage, info in sorted(working, key=lambda x: -max(x[1]["last"].values())):
            tasks = ", ".join(f"`{t}`" for t in info["active_tasks"]) or "нет active-задач"
            state_lines.append(f"- **[{stage}](tracking/stages/{stage}/SUMMARY.md)** — "
                               f"следующий шаг ({info['next_source']}): {info['next_step']} "
                               f"Активные задачи: {tasks}.")
            if info["idle_tasks"]:
                state_lines.append("  Возможно брошены: "
                                   + ", ".join(f"`{t}`" for t in info["idle_tasks"]) + ".")
        state_lines.append("")
    state_lines += [f"Научный приоритет (focus_stage): **{focus} — {project['stages'][focus]['title']}**.", "",
                    focus_rec["summary"] if focus_rec else "Этап ещё не начат.", "",
                    f"Следующий шаг {focus} ({focus_info['next_source']}): {focus_info['next_step']}", "",
                    "| Этап | Статус | Revision | Активных задач |", "|---|---|---|---|"]
    for stage, value in index["stages"].items():
        state_lines.append(f"| [{stage}](tracking/stages/{stage}/SUMMARY.md) | "
                           f"{value['status']} | {value['revision']} | "
                           f"{len(overviews[stage]['active_tasks'])} |")
    state_lines += ["", "Читать сначала эту сводку, затем SUMMARY этапа, где идёт работа. "
                    "Подробные записи и исходы загружать по ID и ссылкам. "
                    "[Протокол трекинга](docs/tracking_protocol.md) · [План](PLAN.md) · "
                    "[Решения](DECISIONS.md).", "",
                    "Активные исполнения проверять по `tracking/runtime.json`; файл является "
                    "реестром, не монитором процессов. Завершение диалога не создаёт фоновую службу."]
    if (root / "docs/context/project_overview.md").is_file() and focus != "S0":
        state_lines += ["", "Научный контекст: [общая картина](docs/context/project_overview.md), "
                        f"[досье фокусного этапа](docs/stages/{focus}.md). "
                        "Досье содержит полный раздел программы и порядок работ."]
    atomic_text(root / "STATE.md", "\n".join(state_lines) + "\n")
    atomic_text(root / "PLAN.md", "\n".join(plan) + "\n")
    atomic_text(root / "DECISIONS.md", "\n".join(decisions) + "\n")
    atomic_text(root / "tracking/index.json", json.dumps(index, ensure_ascii=False, indent=2) + "\n")
