# Инструкция: мониторинг доступных вычислительных станций Vast.ai

Операционная инструкция для read-only мониторинга рынка CPU/compute по списку
`good_cpu.txt`. Документ описывает назначение, запуск, параметры, выходные файлы,
чтение результатов, остановку и типовые ошибки.

Связанные документы: [политика вычислений](../compute_policy.md),
[vastai_cli_cheatsheet.md](../../vastai_cli_cheatsheet.md),
[задание W2-T004](../tasks/W2_market_monitor_v2.md).

> **Главное правило.** Мониторинг не арендует серверы и не выполняет платных
> действий (`paid_actions=0`). Лимит `--max-price` — это предел **поиска**
> предложений, а не разрешение на аренду. Любая аренда требует отдельного
> явного разрешения пользователя по `docs/compute_policy.md`.

---

## 1. Что делает мониторинг

Скрипт периодически опрашивает Vast.ai CLI, оставляет только предложения,
которые соответствуют заданным CPU и границам, и ведёт историю наблюдений:

- какие целевые модели/семейства реально видны;
- по какой цене, с каким числом effective/host CPU, RAM, GPU и диска;
- когда предложение появилось, изменило цену/конфигурацию или пропало;
- долю полных опросов, на которых встречалась каждая цель.

Выдача отражает **заявленную провайдером конфигурацию**, а не измеренную
производительность и не гарантию CPU quota. Дисковые и unknown-контракты
отбрасываются (`resource_type=disk/unknown` — это тома, а не рабочие станции).

---

## 2. Требования и подготовка

1. Запускать интерпретатором проекта из корня репозитория:

   ```powershell
   .\.venv\Scripts\python.exe scripts\vast_track_cpu.py ...
   ```

   Не `python.exe` из системы: зависимости и окружение фиксируются `.venv`.

2. Установленный Vast.ai CLI и ключ в окружении (`VAST_API_KEY`). Ключ не
   печатать, не класть в манифесты и логи. При ошибке доступа использовать
   backoff, не нагрузочный штурм.

3. Прокси-переменные (`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`) внутри скрипта
   вычищаются из окружения дочернего CLI — диагностика подключения описана
   в `vastai_cli_cheatsheet.md`.

4. Источник целевых вариантов — `good_cpu.txt` (пользовательский вход).
   Изменить список = изменить область мониторинга; это фиксируется в
   `coverage.json` через SHA источника.

---

## 3. Архитектура

| Файл | Роль |
|---|---|
| `scripts/vast_track_cpu.py` | **канонический CLI**; читает `good_cpu.txt`, вызывает общий движок |
| `scripts/vast_monitor.py` | совместимый вход в тот же движок (для прежних импортов) |
| `scripts/start_vast_market_monitor.ps1` | фоновый запуск на Windows (48/72 ч) |
| `src/submoon_research/compute/market_monitor.py` | цикл опроса, heartbeat, сроки, статистика, `runtime.json` |
| `src/submoon_research/compute/market_search.py` | read-only запросы к Vast CLI, полнота поиска, фильтр |
| `src/submoon_research/compute/market_store.py` | SQLite-журнал наблюдений и событий |
| `src/submoon_research/compute/cpu_targets.py` | разбор `good_cpu.txt` (сокращения, семейства, Xeon w5/w7/w9) |

`vast_track_cpu.py` и `vast_monitor.py` принимают одинаковый набор флагов —
его формирует `market_monitor.main()`.

---

## 4. Быстрый старт

```powershell
# 1. Посмотреть, какие цели распознаны из good_cpu.txt (без сети)
.\.venv\Scripts\python.exe scripts\vast_track_cpu.py --print-patterns

# 2. Разовый опрос (лёгкая проверка, 1 цикл, без аренды)
.\.venv\Scripts\python.exe scripts\vast_track_cpu.py --once --max-price 1.0

# 3. Короткий сбор: 3 цикла с интервалом 60 с
.\.venv\Scripts\python.exe scripts\vast_track_cpu.py --cycles 3 --interval 60 --max-price 1.0

# 4. Длительный сбор 72 ч
.\.venv\Scripts\python.exe scripts\vast_track_cpu.py --duration-hours 72 --interval 300 --max-price 1.0

# 5. Фоновый запуск на Windows
powershell -File scripts\start_vast_market_monitor.ps1 -Hours 72 -IntervalSeconds 300 -MaxPrice 1.0
```

Только `--once` печатает одну строку JSON-итога; длительный запуск печатает
по строке на цикл и пишет полную историю в папку сессии.

---

## 5. Параметры CLI

| Флаг | По умолчанию | Смысл |
|---|---|---|
| `--max-price` | `1.0` | Верхняя граница `dph_total`, $/ч (предел поиска, не аренды) |
| `--min-cores` | `1.0` | Минимум `cpu_cores_effective` |
| `--min-disk` | `20.0` | Минимум доступного диска, GB |
| `--storage-gb` | `20.0` | Запрашиваемый диск для расчёта цены |
| `--cpu-only` | выкл. | Только `num_gpus=0`; по умолчанию compute с GPU тоже включаются |
| `--whole-machine-only` | выкл. | Только предложения, где `cpu_cores == cpu_cores_effective` |
| `--with-gpu` | — | Совместимость; GPU уже включены по умолчанию |
| `--limit` | `1000` | Желаемый размер выдачи (анонимный endpoint режет до 64/запрос) |
| `--max-queries` | `64` | Лимит запросов на цикл (1…64) |
| `--request-spacing` | `1.0` | Минимальная пауза между запросами, с |
| `--transport` | `anonymous` | `anonymous` — публичный endpoint без ключа и квоты; `cli` — через аккаунт |
| `--allow-cli-fallback` | выкл. | При недоступности анонимного endpoint падать на CLI (тратит квоту) |
| `--interval` | `300.0` | Пауза между циклами, с (≥15) |
| `--duration-hours` | `72.0` | Срок сессии, ч (0…72] |
| `--cycles` | — | Ограничить число циклов |
| `--once` | — | Ровно один цикл (эквивалент `--cycles 1`) |
| `--output` | авто | Папка сессии; по умолчанию `data/interim/vast_market/<UTC>-<id>` |
| `--resume` | — | Продолжить существующую незавершённую сессию |
| `--request-stop` | — | Папка сессии: мягкая остановка без убийства процесса |
| `--stats` | — | Папка с `market.sqlite3`: пересчитать статистику без сети |
| `--print-patterns` | — | Показать разобранные цели из `good_cpu.txt` без сети |

---

## 6. Как отбираются предложения

Предложение попадает в результат (`latest.txt`/`latest.json`) только если
одновременно выполнено:

1. `resource_type` ∈ {`cpu`, `compute`, `gpu`}; `disk`/`unknown` — отклоняются;
2. для `gpu` — `num_gpus > 0`; при `--cpu-only` остаются только `num_gpus=0`;
3. `cpu_name` совпадает с одной из целей `good_cpu.txt`;
4. `rentable=true` и `rented=false`;
5. `dph_total` ∈ [0, `--max-price`];
6. `cpu_cores_effective` ≥ `--min-cores`;
7. `disk_space` ≥ `--min-disk`;
8. при `--whole_machine-only` — `cpu_cores == cpu_cores_effective`.

Причины отклонения считаются и сохраняются в `latest.json` → `rejected`.

Цена приводится к запрошенному диску: `hourly_total_usd` = базовое +
хранилище; `hourly_per_effective_cpu_usd` = цена / effective CPU.
RAM в `candidate` пересчитывается из `cpu_ram/1000` (как показывает CLI 0.3.1);
`0` трактуется как «неизвестно» (`None`).

---

## 7. Полнота поиска

Транспорт по умолчанию — **анонимный** `POST https://console.vast.ai/api/v0/bundles/`:
без API-ключа, поэтому **не расходует суточную квоту поисковых строк аккаунта**.
Публичный endpoint отдаёт **не более 64 строк на запрос** и не поддерживает offset,
поэтому полнота достигается разбиением ценового диапазона; насыщенный бакет
(`len == 64`) делится дальше. При бюджете 64 запроса за цикл прогон находит
~130–150 предложений целевых CPU. `--transport cli` возвращает аккаунтный
поиск (до 1000 строк/запрос, но тратит квоту).

Ни CLI, ни публичный endpoint не поддерживают offset, поэтому `collect()`:

- ищет по числу GPU отдельно (`num_gpus=0`, затем `num_gpus>=1`);
- при насыщении ценового диапазона делит его пополам (до глубины 12), а затем
  делит по числу GPU;
- уважает бюджет `--max-queries` и `cycle_budget` (240 с), паузу
  `--request-spacing`, `timeout` 60 с;
- при `rate_limited`/`auth` прекращает домены и помечает цикл неполным.

`complete=true` означает: все запросы успешны и нет незакрытых фрагментов.
Анонимный транспорт честно помечает `complete=false`, пока в каком-то бакете
остаётся >64 строк: это признак неполного покрытия, а не ошибка.
**Ошибка или неполный цикл не считается исчезновением предложений**: такие
циклы исключаются из знаменателя доступности. Остаточное ограничение backend
помечается `saturated_price_bucket`; абсолютный охват невидимых предложений
не обещается.

---

## 8. Выходные файлы сессии

Папка: `data/interim/vast_market/<UTC>-<id>/` (внутри проекта).

| Файл/папка | Содержимое |
|---|---|
| `good_cpu.snapshot.txt` | Снимок источника целей на старте |
| `coverage.json` | **Лог нужных вариантов**: SHA источника, число целей, список паттернов |
| `manifest.json` | Идентичность области, срок, статус, PID, `paid_actions`, версии кода |
| `heartbeat.json` | Последний цикл, статусы запросов, задержка до следующего, deadline |
| `market.sqlite3` | Все циклы, запросы, наблюдения, события, active/target_sightings |
| `snapshots/NNNNNN.json.gz` | Полный сжатый снимок каждого цикла |
| `events.jsonl` | Появления/исчезновения/изменения цены и конфигурации |
| `latest.json` / `latest.txt` | Текущие подходящие предложения и причины отклонения |
| `statistics.json` | Сводная статистика по циклам, моделям и целям |
| `checksums.json` | SHA-256 всех файлов сессии на момент завершения |
| `code/` | Копия кода движка для воспроизводимости |
| `launch.pid`, `collector.lock` | PID и защита от второго сборщика в папке |

Формат `latest.txt`:

```
ID | CPU | $/h total | effective / host logical CPU | RAM GB | GPU | location
```

`statistics.json` (`--stats <папка>`) содержит `total_polls`,
`complete_polls`, `incomplete_or_error_polls`, `request_status_counts`,
`target_coverage` (по каждой цели: доля полных опросов) и `models`
(для каждой модели: `availability_fraction_of_complete_polls`,
`unique_machines`, `unique_allocations`, `min/median/max_hourly_usd`).
Цена дедуплицируется до machine/allocation, чтобы один хост с несколькими GPU
не считался несколькими станциями.

---

## 9. Чтение результатов

### 9.1. Отчёт по моделям и ценам (рекомендуется)

```powershell
# Полный отчёт по конкретной сессии
.\.venv\Scripts\python.exe scripts\vast_market_report.py data\interim\vast_market\<session>

# Самая свежая сессия, только 15 самых частых моделей
.\.venv\Scripts\python.exe scripts\vast_market_report.py --latest --top 15

# Машиночитаемый JSON
.\.venv\Scripts\python.exe scripts\vast_market_report.py --latest --json
```

Отчёт сортирует модели по частоте появления (числу циклов с данными), затем по
минимальной цене, и выводит: число циклов с моделью, долю циклов с данными, число
разных машин, `min/avg/median/max` цены `$/ч` и `min/avg` цены за effective CPU.
Учитываются и частичные циклы (анонимный транспорт штатно даёт `complete=false`
из-за лимита 64 строк), поэтому в шапке видно «циклов с данными: X (полных Y из Z)». `--latest` выбирает самую свежую сессию **с наблюдениями**, а не пустую.
Дополнительно перечисляются цели из `good_cpu.txt`, ни разу не встреченные.

Как читать результат:

- **частые и дешёвые сверху** — на них и стоит ориентироваться;
- `Опросов` = сколько раз модель реально появлялась (частоту сравнивать только
  при большом числе полных опросов);
- `Мин $/ч` — лучшая увиденная цена; `Сред $/ч`/`Медиана` — типичный уровень;
- большая разница между min и avg означает нестабильную цену или разные
  аллокации; смотреть `Мин $/ядро` для сравнения станций с разным числом ядер;
- цели в «ни разу не встреченных» — повод увеличить срок сбора или проверить
  список, а не доказательство отсутствия на рынке.

### 9.2. Штатная статистика и файлы

```powershell
# Сводка без сети
.\.venv\Scripts\python.exe scripts\vast_track_cpu.py --stats data\interim\vast_market\<session>

# Текущая таблица подходящих предложений
Get-Content data\interim\vast_market\<session>\latest.txt

# Разобранные цели и их SHA
Get-Content data\interim\vast_market\<session>\coverage.json
```

Пример SQL-запросов (sqlite3):

```sql
-- цена по циклам для конкретной модели
SELECT c.utc, o.machine_key, o.price
FROM observations o JOIN cycles c ON c.id=o.cycle_id
WHERE o.cpu_key LIKE '%9950x%' AND c.complete=1 ORDER BY c.id;

-- сколько полных опросов видели цель
SELECT t.target, count(*) FROM target_sightings t
JOIN cycles c ON c.id=t.cycle_id WHERE c.complete=1 GROUP BY t.target;
```

---

## 10. Жизненный цикл и остановка

- Сессия начинается манифестом, копией кода и `coverage.json`; активная сессия
  регистрируется в `tracking/runtime.json` (kind `read_only_market_monitor`,
  `task_id=W2-T004`, `paid_actions=0`).
- Мягкая остановка:

  ```powershell
  .\.venv\Scripts\python.exe scripts\vast_track_cpu.py --request-stop data\interim\vast_market\<session>
  ```

  Создаётся `stop.requested`, цикл завершается штатно, статус `stopped`.
- Продолжение после перерыва: `--resume` с той же папкой. Нельзя менять
  `good_cpu.txt`, конфигурацию или версию кода — при расхождении движок
  откажется смешивать статистику. Продолжать завершённую сессию запрещено.
- При завершении/остановке/ошибке пишется `checksums.json`, `statistics.json`,
  статус в `manifest.json`, и запись удаляется из `runtime.json`.
- Лимит выходов — 2 GiB на сессию; при превышении сбор останавливается.
- Heartbeat и backoff: при ошибках задержка растёт до 1800 с, heartbeat
  продолжает обновляться (проверяемость), но серия не «штормит» API.

Прокси-предупреждение: `stop.requested` — единственный безопасный способ
остановки; `kill` процесса может оставить `collector.lock`.

---

## 11. Фоновый запуск

```powershell
powershell -File scripts\start_vast_market_monitor.ps1 `
    -Hours 72 -IntervalSeconds 300 -MaxPrice 1.0
```

Скрипт создаёт сессию в `data/interim/vast_market/`, а stdout/stderr и
`launch.json` (PID, срок, параметры) — в
`scratch/vast_market_controls/<session>/`. `Hours` только 48 или 72,
`IntervalSeconds` 60…3600, `MaxPrice` ≤ 1.0. Это read-only запуск без аренды.

---

## 12. Ограничения и что мониторинг НЕ доказывает

- Это сведения **провайдера** о конфигурации и цене; CPU quota, скорость ядра
  и реальная доступность не измерены.
- Наблюдение между снимками не видно; отсутствие в двух полных опросах не
  доказывает аренду или снятие железа.
- Число GPU-офферов одного `machine_id` — не число независимых станций.
- Рыночный backend может ограничивать выдачу; полнота относится к проверяемым
  запросам, а не ко всему рынку.
- Мониторинг не заменяет численный бенчмарк и не является допуском W2.

---

## 13. Типовые ошибки

| Симптом | Причина / решение |
|---|---|
| `JSONDecodeError: Unexpected UTF-8 BOM` при старте | `tracking/runtime.json` имел BOM. Исправлено чтением `utf-8-sig` и добавлением регрессионного теста (`tests/unit/test_vast_market_monitor.py`). Если файл снова появится с BOM — нормализовать `runtime.json`. |
| `Для существующей сессии нужен --resume` | Папка `--output` уже существует. Указать новую или добавить `--resume`. |
| `Версия кода изменилась; создать новую сессию` | Код движка менялся между стартом и resume. Статистику не смешивать: новая сессия. |
| `Истёк исходный срок сессии` | Deadline в `manifest.json` прошёл; новая сессия с новым сроком. |
| Пустой результат, `complete=false` | Ошибки/rate limit/таймаут запросов; смотреть `heartbeat.json` и `request_status_counts`. Не трактовать как исчезновение. |
| `rate_limited` | Штатный backoff; увеличить `--request-spacing`/`--interval`, не повторять штормом. Анонимный endpoint может резать по IP. |
| `found: 0`, `complete=false` | Анонимная выдача — 64 самых дешёвых; при малом `--max-queries` поиск не доходит до нужных цен. Поднять `--max-queries` (дефолт 64). |
| `cli_unavailable` | Vast CLI не установлен/не в PATH; ключ — через окружение. |
| Незавершённая сессия с `collector.lock` | Процесс упал не в `finally`. Убедиться, что PID неактивен, затем удалить папку сессии или его lock. |

---

## 14. Проверки перед долгим сбором

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_vast_market_monitor.py -q
.\.venv\Scripts\python.exe -m ruff check src\submoon_research\compute scripts\vast_track_cpu.py
.\.venv\Scripts\python.exe scripts\project.py validate
```

Затем один `--once`, убедиться, что `complete=true` и `latest.txt` заполнен,
и только после этого запускать 48/72 ч.
