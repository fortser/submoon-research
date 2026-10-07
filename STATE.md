# Состояние исследования

Производная точка входа. Не редактировать вручную; обновлять журналы через `scripts/project.py track add`, затем сводки восстанавливаются автоматически.

Текущий фокус: **W1 — Мера и статистический дизайн**.

Статистический протокол T=10000 лет и разработочный комплект сохранены. Для завершения W1 созданы прототипы ускорения A/B/C, но численная приёмка/замеры ещё не выполнены; реальный пилот, дисперсия, прогнозы H3 и freeze открыты.

Следующий шаг: W2-T003: удалённо проверить и сравнить A/B/C; затем допуск реального пилота W1. Передача 2026-10-06 содержит файлы и разрешённый бюджет.

| Этап | Статус | Revision |
|---|---|---|
| [S0](tracking/stages/S0/SUMMARY.md) | complete | 28 |
| [W0](tracking/stages/W0/SUMMARY.md) | complete | 124 |
| [W1](tracking/stages/W1/SUMMARY.md) | active | 25 |
| [W2](tracking/stages/W2/SUMMARY.md) | active | 46 |
| [W3](tracking/stages/W3/SUMMARY.md) | blocked | 1 |
| [W4](tracking/stages/W4/SUMMARY.md) | planned | 1 |
| [W5](tracking/stages/W5/SUMMARY.md) | planned | 1 |
| [W6](tracking/stages/W6/SUMMARY.md) | planned | 1 |
| [W7](tracking/stages/W7/SUMMARY.md) | planned | 1 |
| [W8](tracking/stages/W8/SUMMARY.md) | blocked | 1 |
| [W9](tracking/stages/W9/SUMMARY.md) | planned | 1 |
| [W10](tracking/stages/W10/SUMMARY.md) | planned | 1 |

Читать сначала эту сводку, затем SUMMARY текущего этапа. Подробные записи и исходы загружать по ID и ссылкам. [Протокол трекинга](docs/tracking_protocol.md) · [План](PLAN.md) · [Решения](DECISIONS.md).

Активные исполнения проверять по `tracking/runtime.json`; файл является реестром, не монитором процессов. Завершение диалога не создаёт фоновую службу.

Научный контекст: [общая картина](docs/context/project_overview.md), [досье текущего этапа](docs/stages/W1.md). Досье содержит полный раздел программы и порядок работ; для S0 вместо досье используются инфраструктурные протоколы.
