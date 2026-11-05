# W2-T073: инвентаризация потребителей, лимиты и коллизии

Evidence/run_id: `W2_T073_limits_20261103T110653Z`. Продолжение C130, без расширения D051 на новые научные consumers. Проверки C131 (writer/legacy) и C132 (граница reader); проблема I046.

## 1. Границы инвентаризации

Выполнен статический поиск RunContext, manifest.json, code_sha256, artifacts_sha256, initial_code_snapshot и schema_version в текущих `src`, `scripts`, `tests`, затем прочитаны найденные пути чтения. Сохранены `consumers_rg.txt`, `schema_versions_rg.txt`, `tests_consumers_rg.txt`; источники/SHA — в manifest.json evidence. Это инвентаризация штатного исходного дерева, не доказательство отсутствия динамических импортов или внешних/исторических consumers в scratch, архивных snapshots и notebooks.

### Прямые потребители RunContext

| Consumer | Что фактически делает | Отношение к 0.3 |
|---|---|---|
| `contracts/freeze.py` | Gate допуска: версии/marker (D051), status/validation, свидетельства, SHA перечисленного кода/артефактов/входов | Адресно проверен C130; текущий регрессионный профиль снова прошёл |
| `scripts/analyze_w1.py::main` | Повторный анализ; проверяет data_kind, привязывает SHA outcomes/config, затем aggregate | Не проверяет версию/marker или code_sha256; C132 подтвердил достижение границы aggregate при противоречивом 0.3 |
| `scripts/acquire_planetary_alignment.py::main --replay` | Читает предыдущий manifest, требует completed/passed, привязывает артефакты и использует ответы/сырые данные | Статически нет проверки marker; replay и AstroBridge не запускались |
| `scripts/acquire_geometric_states.py --replay` | Проверяет перечисленные SHA артефактов, completed/passed и совпадение конфигурации; копирует приобретённые данные | Статически нет проверки marker; replay не запускался |
| `scripts/verify_w0_completion.py::main` | Приёмка baseline: completed/passed, входы/артефакты, затем содержательные научные проверки | Статически нет проверки marker; приёмка W0 не запускалась |
| `scripts/review_w0_quality.py::inspect_provenance` | Исторический диагностический отчёт: статусы, перечисленные SHA/восстановимость, неперечисленные файлы | Не gate; игнорирует marker и не доказывает полноту initial snapshot. Не обязан запрещать просмотр pending/failed |
| `scripts/review_w0_independent.py::main` | Читает inputs_sha256 намеренно неуспешного L1 CLI-контроля | Диагностическое чтение failed-run законно; не превращать этот путь в completed-only gate |
| `tests/integration/test_l1_acceptance.py` и provenance/freeze тесты | Проверяют реальные CLI статусы/inputs и синтетический lifecycle | Два штатных CLI-контроля выполнены; полный L1 suite не запускался |

### Producers и иные схемы — не смешивать

19 файлов используют RunContext как producer: 15 scripts (`analyze_w1`, `acquire_planetary_alignment`, `acquire_geometric_states`, `adopt_nominal_model`, `benchmark_w1`, `run_w1_pilot`, `run_w0_baseline`, `verify_nominal_controls`, `verify_sampling_controls`, `verify_l1_specification`, `verify_analytic_controls`, `verify_remediation`, `verify_w1_restart`, `verify_w0_completion`, `verify_w1_statistics`) и четыре workflows (`w2_campaign`, `w2_longbench`, `audit_runner`, `smoke`). Само создание RunContext не является дополнительным reader-gate. Наличие таких CLI не разрешает научную серию.

`w2_campaign` и `w2_longbench` создают RunContext для **стадий**, но campaign-envelope имеют собственный набор полей; summarize использует их stages и summary.json, а не индекс начального снимка RunContext. Их manifest нельзя автоматически считать 0.2/0.3.

`physics_audit_v1`, `phase3_audit`, `phase3_v2_input`, `b144_scientific` и `scripts/phase3_convergence.py`, `b144_regress.py` читают специализированные PH3/B144 manifests (units_file_sha256, block, pinned hash и т. п.). Market monitor, hardware trial, release.manifest, frozen inputs/bundles и другие snapshot/launch manifests — отдельные форматы. Поиск точных schema_version-проверок не выявил ещё одной строгой схемы RunContext помимо D051; это не доказательство совместимости всех динамических маршрутов.

## 2. C132: фактический результат проверки analyze_w1

Helper импортирует настоящий `scripts/analyze_w1.py`, использует настоящий RunContext и InputLedger в собственном временном synthetic workspace. `__file__` перенаправлен только в памяти; пакет для копирования и CLI-путь синтетические. `aggregate` заменён барьером, который сразу поднимает исключение. **Ни aggregate, ни интервалы, ни научная интеграция не выполнялись.** Исходные файлы consumer не изменены.

| Вход | Факт |
|---|---|
| Явный 0.2 без marker | Достигнута граница aggregate |
| Корректный complete 0.3 | Достигнута граница aggregate |
| pending 0.3 при completed/passed | Достигнута граница aggregate |
| complete с ожидаемым отсутствующим путём | Достигнута граница aggregate |
| complete с неверным published_count | Достигнута граница aggregate |
| failed/not_run при complete | Достигнута граница aggregate |
| Неверный SHA outcomes | ValueError до границы aggregate |

Итак, проверка байтов outcomes работает, а новый контракт снимка не участвует во входном контроле. Достижение границы не равно успешному анализу: rows здесь пустые, последующие содержательные проверки намеренно не выполнялись. В частности, результат **не** доказывает выпуск ложного научного вывода или повреждение исторических данных.

Случай failed/not_run фиксирует существующую политику, но сам по себе не объявлен дефектом: анализ частичных исходов с unknown-bounds может быть допустим и требует явного решения. Complete начального снимка и completed всего научного запуска — разные требования. Нельзя молча вводить completed-only gate для любого анализа.

По ТЗ и D051 изменение этих readers не разрешено. Приёмка T073 вновь остановлена до пересмотра области, reader-код не изменялся. Статические кандидаты W0 не объявляются динамически проверенными failures.

## 3. C131: writer, лимиты, коллизии и legacy

Добавлены 14 постоянных параметризованных случаев в `tests/unit/test_provenance_batch.py`; production-код в этом продолжении не менялся:

- Выходной бюджет во время копирования и принудительная проверка после всего staging при отложенном обычном опросе: реальная запись >1 MiB, нет тела/commit, индекс не опубликован частично.
- Детерминированная имитация истечения wall после первой реальной копии; лимиты выше глобального профиля отклоняются до копирования.
- Ошибки реального Path.read_bytes на первом/втором файле и KeyboardInterrupt: исходное исключение сохранено, failed/cancelled, snapshot failed.
- Инъекция ENOSPC после частичной записи второй копии: аварийный run сохранён; нет ложного полного индекса; соседние manifest/temp не меняются.
- Существующий run-directory и повторный вход в завершённый run не перезаписываются. Искусственная коллизия имён через _code_name отклоняется до первой копии. В этом раннем случае manifest ещё не опубликован; не заявляется обязательный failed-manifest для ошибок инвентаризации до публикации.
- Установленный пакет вне workspace даёт канонические src/submoon_research пути.
- SHA всех записанных артефактов, inputs и scientific_id воспроизводятся по сохранённым байтам; git/finished поля присутствуют.

Позитивы используют настоящие файловые записи/atomic_text. Инъекции времени и ENOSPC не означают фактическое исчерпание диска/ожидание 120 s. Проверка бюджета идёт между операциями: не доказаны жёсткий предел RSS, отсутствие краткого превышения выходного размера при одной копии/финализации или power-loss durability. Проверка коллизии имени — инъекция отображения, не перебор всех alias/symlink/case-folding файловой системы.

| Проверка | Результат |
|---|---|
| Новый writer/provenance профиль Windows | 37 passed |
| Совместный provenance + freeze + W1 protocol + release Windows | **111 passed**, pytest 6.36 s |
| Тот же профиль Ubuntu-24.04 | **111 passed**, pytest 11.65 s |
| Штатный L1 CLI rotating_frame и canonical offline contract | **2 passed**, pytest 6.16 s; без интеграций |
| Мутация пропуска force budget только в памяти | Один ожидаемый failure, 0 setup/teardown errors; helper rc=0 |
| Реальный завершённый legacy L0 0.2 | `L0-20261101T184623Z-c3f2af79`: 90 code SHA и 100 artifact SHA совпали; manifest SHA неизменен `74800c6f25e4d741c0284eec3c275c82f45ae5af8609c59e95f76b29b40d58e3` |
| Ruff src/scripts/tests, LSP тестов, validate | passed |
| Свежий L0 smoke | passed, `L0-20261103T111338Z-5dce14e9` |

Legacy проверен read-only; полнота его начального набора не выводится из отсутствия marker. Настоящий freeze не запускался. Общий pytest не запускался и его стоимость здесь не измерена.

## 4. Бюджет и передача

600 s T073 не обнулялись: прежний учёт 292.623 s округлён до резерва **300 s**, новые попытки — в attempts.jsonl. По-прежнему один worker/поток, ≥5 GiB свободного диска, ≤256 MiB evidence, контроль 2 GiB наблюдаемого Windows-дерева. WSL явно Ubuntu-24.04 с timeout; Windows sampling не измеряет полный Linux RSS и не доказывает отсутствие ненаблюдавшихся Linux-потомков. Наблюдаемых собственных Windows survivors нет. SHA исходников/копий и окружение — в manifest.json.

Следующий шаг: согласовать отдельное ограниченное расширение на **четыре научных consumer-пути** (analyze_w1, оба acquisition replay, verify_w0_completion), используя общий контракт RunContext с проверкой сохранённых копий через существующий ledger; определить роль status отдельно для анализа частичных исходов. Для двух диагностических reviewers сохранить право читать pending/failed и явно не считать их отчёты доказательством полноты снимка. Специализированные форматы, архивы и физику не менять. До такого решения T073 blocked; I045 остаётся fixed без независимого review. C120/C125, физический restart/high-e и W1/W3 не закрываются.
