# Состояние исследования

Производная точка входа. Не редактировать вручную; обновлять журналы через `scripts/project.py track add`, затем сводки восстанавливаются автоматически.

Текущий фокус: **W0 — Аудит данных и базовой постановки**.

W0-T004 выполнена: SHA фактических входов, закрытые схемы L1 v0.2/v0.3, отрицательные CLI-пробы и общий lifecycle проверены; 101 pytest проходит. Генератор W1 проверен на синтетике. W0/реальный пилот не завершены: научные входы и строгий V02 остаются открыты.

Следующий шаг: Проверить резервную копию выпуска на C; следующий научный шаг — строгий V02 и внешние GM/J2/полюса/совместимость состояний.

| Этап | Статус | Revision |
|---|---|---|
| [S0](tracking/stages/S0/SUMMARY.md) | complete | 28 |
| [W0](tracking/stages/W0/SUMMARY.md) | active | 92 |
| [W1](tracking/stages/W1/SUMMARY.md) | active | 9 |
| [W2](tracking/stages/W2/SUMMARY.md) | active | 10 |
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

Научный контекст: [общая картина](docs/context/project_overview.md), [досье текущего этапа](docs/stages/W0.md). Досье содержит полный раздел программы и порядок работ; для S0 вместо досье используются инфраструктурные протоколы.
