# Трекинг проекта

Протокол: [docs/tracking_protocol.md](../docs/tracking_protocol.md).

`project.yaml` — архитектура и фокус. `stages/<stage>/journal.jsonl` — изменения состояния. `SUMMARY.md` — ограниченная сводка, `RECORDS.md` — полный текущий индекс. `index.json` — полная машинная проекция для инструментов, не для загрузки в контекст по умолчанию.

JSON Schema: `record.schema.json` и `event.schema.json`. Пример ввода: `record.example.json`. CLI проверяет также существование локальных refs, revision и владельца активной задачи; одной проверки JSON Schema для записи недостаточно.

Источник фактов — журналы и доказательства. Рендер восстанавливает Markdown, не изменяя историю. Глобальные STATE/PLAN/DECISIONS являются представлениями этого же состояния.
