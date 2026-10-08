# Все записи W1

Revision: 27.

## Задачи

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-T001 | ready | W1-001 — мера и статистический дизайн: Разработочный комплект W1 и ограниченные проверки W2 реализованы. Полный пилот, длинный численный допуск и freeze остаются открыты; см. итоговый отчёт. | W2-T003: быстрый проверенный режим; затем реальный пилот на T=10000 лет и freeze W1. | [docs/handoffs/W0_completion_to_W1_W2_v1.md](../../../docs/handoffs/W0_completion_to_W1_W2_v1.md), [docs/tasks/W1-001.md](../../../docs/tasks/W1-001.md), [results/evidence/W0_completion_v1.json](../../../results/evidence/W0_completion_v1.json), [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md), [results/evidence/W1_implementation_v1.json](../../../results/evidence/W1_implementation_v1.json) |

## Решения

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-D001 | accepted | Общий T=10000 лет, полный состав групп, пилот трёх хозяев и правила H1–H3: Пользователь согласовал W1 с необходимыми проверками W2, T=10000 юлианских лет и полный протокол с пилотом на Япете/Ганимеде/Гималии. Зарегистрированы H1/H2/H3, прямые меры, split, seeds, консервативные интервалы и обработка unresolved. Протокол development: ни короткий benchmark, ни synthetic coverage не открывают freeze или production. | Измерить ограниченную стоимость общего T и проверить события/рестарт. | [docs/tasks/W1_implementation_protocol_v1.md](../../../docs/tasks/W1_implementation_protocol_v1.md), [configs/sampling/W1_design_v1.yaml](../../../configs/sampling/W1_design_v1.yaml) |

## Проверки

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-C001 | passed | Условные меры и генератор на синтетике: W1-sampling-20261005T093441Z-e7b1dbac: три меры × восемь рандомизаций ×64 старта. Нормировки, изотропность, энергия/момент, вырождения, знаменатель и повторяемость проверены. Это не реальный пилот и не полное V06. | Принять реальные domain/GM и завершить дизайн/проверку покрытия после пилота. | [runs/W1-sampling-20261005T093441Z-e7b1dbac/validation.json](../../../runs/W1-sampling-20261005T093441Z-e7b1dbac/validation.json), [reports/scientific/W0_remediation_20261005.md](../../../reports/scientific/W0_remediation_20261005.md) |
| W1-C002 | passed | Генератор и нормировки на реальных номинальных областях: Три хозяина × три меры × две независимые рандомизации ×64 старта: нормировки, ограничения контакта, повторяемость и независимые E/L прошли. Не оценка выживаемости и не полный V06. | Зафиксировать общий T и H1–H3; затем разработочный пилот стоимости/дисперсии. | [results/evidence/W0_completion_v1.json](../../../results/evidence/W0_completion_v1.json), [docs/handoffs/W0_completion_to_W1_W2_v1.md](../../../docs/handoffs/W0_completion_to_W1_W2_v1.md) |
| W1-C003 | passed | V06: консервативные интервалы на независимых synthetic-контролях: Пять функций ×256 повторов отдельно development/validation; Hoeffding прошёл объявленный критерий покрытия. Student-t для восьми scrambles не прошёл ряд контролей и не допущен. Это ограниченный V06: не гарантия покрытия всех физических индикаторов и не измерение динамической дисперсии. | Сохранить консервативный метод; реальную дисперсию получить после допуска пилота. | [runs/W1-coverage-20261005T164953Z-ddaa0340/validation.json](../../../runs/W1-coverage-20261005T164953Z-ddaa0340/validation.json), [runs/W1-coverage-20261005T164953Z-ddaa0340/results/coverage.json](../../../runs/W1-coverage-20261005T164953Z-ddaa0340/results/coverage.json), [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md) |

## Результаты

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-R001 | validated | Принят вход W0 для статистического дизайна: L1-nominal-v0.4.1, aligned-v3, реальные домены/базисы и короткая стоимость приняты с SHA. Разработочная сетка 36 не даёт общей доли; общий T, H1–H3, дисперсия, интервалы и freeze предстоит определить. | Зафиксировать общий T и H1–H3; затем разработочный пилот стоимости/дисперсии. | [results/evidence/W0_completion_v1.json](../../../results/evidence/W0_completion_v1.json), [docs/handoffs/W0_completion_to_W1_W2_v1.md](../../../docs/handoffs/W0_completion_to_W1_W2_v1.md) |
| W1-R002 | validated | Разработочный комплект W1: контракт, анализ, события и защищённый пилотный маршрут: Реализованы T/меры/split/seeds, знаменатели, unknown-bounds, H1/H2/H3 и усиленный freeze guard. Проверки: 144 pytest, Ruff, validate, smoke; coverage и single-worker restart passed. Принята реализация в ограниченной области; полный научный W1 не завершён. | Продолжить W2 ускорением и длительными контролями; W1 freeze после пригодного пилота. | [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md), [results/evidence/W1_implementation_v1.json](../../../results/evidence/W1_implementation_v1.json) |

## Ошибки и проблемы

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-I001 | open | Нет пригодного полного пилота на T=10000 лет и окончательного бюджета точности: Код и правила H1–H3 реализованы, но текущий DOP853 слишком дорог для масштабирования. Реальная дисперсия, количественные прогнозы H3 и freeze отсутствуют. Гарантированные интервалы консервативны; 8 scrambles не обеспечивают автоматически 1 п.п. | Сначала быстрый проверенный W2-режим, затем реальный пилот и окончательный дизайн. | [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md), [results/evidence/W1_implementation_v1.json](../../../results/evidence/W1_implementation_v1.json) |

## Гипотезы и альтернативы

| ID | Статус | Запись | Следующий шаг | Детали/доказательства |
|---|---|---|---|---|
| W1-H001 | proposed | H1: благоприятная группа отделяется от контроля: Проверить разность при каждой основной мере; 3 п.п. — проектный практически значимый масштаб, не измеренный эффект. Правила решения зарегистрированы в W1-D001; проверка гипотезы ещё не выполнена. | Получить пригодный реальный пилот; заполнить количественные прогнозы и freeze до основных/отложенных исходов. | [docs/tasks/W1-001.md](../../../docs/tasks/W1-001.md), [docs/predictions/W1_H1_H2_H3_v1.md](../../../docs/predictions/W1_H1_H2_H3_v1.md), [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md) |
| W1-H002 | proposed | H2: внутренний порядок устойчив к мере: Альтернатива — перестановки ранга при смене меры и фаз. До анализа зафиксировать семейство сравнений и интервалы. Правила решения зарегистрированы в W1-D001; проверка гипотезы ещё не выполнена. | Получить пригодный реальный пилот; заполнить количественные прогнозы и freeze до основных/отложенных исходов. | [docs/tasks/W1-001.md](../../../docs/tasks/W1-001.md), [docs/predictions/W1_H1_H2_H3_v1.md](../../../docs/predictions/W1_H1_H2_H3_v1.md), [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md) |
| W1-H003 | proposed | H3: геометрия объясняет часть различий: Простой геометрический предсказатель сравнить с физическим остатком на отложенных хозяевах; независимая проверка ещё не заморожена. Правила решения зарегистрированы в W1-D001; проверка гипотезы ещё не выполнена. | Получить пригодный реальный пилот; заполнить количественные прогнозы и freeze до основных/отложенных исходов. | [docs/tasks/W1-001.md](../../../docs/tasks/W1-001.md), [docs/predictions/W1_H1_H2_H3_v1.md](../../../docs/predictions/W1_H1_H2_H3_v1.md), [reports/scientific/W1_implementation_20261005.md](../../../reports/scientific/W1_implementation_20261005.md) |
