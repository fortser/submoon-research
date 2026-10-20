# Состояние исследования

Производная точка входа. Не редактировать вручную; обновлять журналы через `scripts/project.py track add`, затем сводки восстанавливаются автоматически.

## Где сейчас идёт работа

- **[W2](tracking/stages/W2/SUMMARY.md)** — следующий шаг (W2-STAGE): Выполнить отдельный короткий W2-P012: M0 точный native контроль; MP полиномиальная память; MC csx/csv; MD dt_last_done; MA сочетание. До интеграции проверить маски/ABI/неизменность x/v и параметров, MA сравнить с P00. Не менять production B/guard; ≤600s, без аренды/часового прохода. Активные задачи: `W2-T008`.
  Возможно брошены: `W2-T008`.

Научный приоритет (focus_stage): **W1 — Мера и статистический дизайн**.

Протокол T=10000 лет, H1–H3, интервалы и freeze-guard реализованы; synthetic coverage прошёл. Оценка CPU-матрицы W2-T006 ≈1.14 машино-ч относится к историческому dop853_jit и 5-летнему бенчмарку; её нельзя прямо переносить на D или будущий гибрид. P2-2a принят как данные, но численный допуск W2 не получен; OPT-01–05 устранили файловые риски, не научные блокеры. Реальный пилот W1, дисперсия, прогнозы H3, новая модель стоимости и freeze открыты.

Следующий шаг W1 (W1-STAGE): Дождаться численного допуска и измеренной стоимости фактически выбранного режима W2 на длинном горизонте; затем реальный пилот W1 и freeze. Сам пакет OPT-01–05 пилот не разрешает.

| Этап | Статус | Revision | Активных задач |
|---|---|---|---|
| [S0](tracking/stages/S0/SUMMARY.md) | complete | 37 | 0 |
| [W0](tracking/stages/W0/SUMMARY.md) | complete | 125 | 0 |
| [W1](tracking/stages/W1/SUMMARY.md) | active | 28 | 0 |
| [W2](tracking/stages/W2/SUMMARY.md) | active | 450 | 1 |
| [W3](tracking/stages/W3/SUMMARY.md) | blocked | 1 | 0 |
| [W4](tracking/stages/W4/SUMMARY.md) | planned | 1 | 0 |
| [W5](tracking/stages/W5/SUMMARY.md) | planned | 1 | 0 |
| [W6](tracking/stages/W6/SUMMARY.md) | planned | 1 | 0 |
| [W7](tracking/stages/W7/SUMMARY.md) | planned | 1 | 0 |
| [W8](tracking/stages/W8/SUMMARY.md) | blocked | 1 | 0 |
| [W9](tracking/stages/W9/SUMMARY.md) | planned | 1 | 0 |
| [W10](tracking/stages/W10/SUMMARY.md) | planned | 1 | 0 |

Читать сначала эту сводку, затем SUMMARY этапа, где идёт работа. Подробные записи и исходы загружать по ID и ссылкам. [Протокол трекинга](docs/tracking_protocol.md) · [План](PLAN.md) · [Решения](DECISIONS.md).

Активные исполнения проверять по `tracking/runtime.json`; файл является реестром, не монитором процессов. Завершение диалога не создаёт фоновую службу.

Научный контекст: [общая картина](docs/context/project_overview.md), [досье фокусного этапа](docs/stages/W1.md). Досье содержит полный раздел программы и порядок работ.
