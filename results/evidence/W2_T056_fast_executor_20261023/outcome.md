# W2-T056: быстрый аппаратный исполнитель подготовлен

owner/actor: `pi-trial-event16-20261023` (реализация и приёмка). Ни одного платного контракта в этой задаче не создано.

## Что сделано

- `scripts/vast_hardware_trial.py` — `prepare` без сети/расходов, `run` по сохранённому новому сигналу пользователя, `recover` только для сверки/удаления уже существующей попытки.
- `src/submoon_research/compute/hardware_trial.py` — один durable source/episode token, OS-lock, активная задача/owner, параллельные свежие gates аккаунта и оффера, 30-минутный дедлайн, один create без retry, reconciliation по label, cleanup в `finally`, runtime без потери неизвестного instance_id, evidence даже при отказе диска.
- `src/submoon_research/compute/trial_transport.py` — прямой REST без CLI create, Authorization вне URL/argv, bounded response, безопасные коды ошибок, завершаемые дочерние процессы для REST/SSH, SSH со сверкой SHA upload/download и без сохранения недоверенного stderr, явное сравнение видимых CPU/RAM с заявленными.
- `scripts/vast_hardware_probe.py` — ограниченный Linux probe: topology/affinity/cgroup, 4/30/30-секундные нагрузки, 512 MiB page-touch, 64 MiB fsync+SHA; ограничения записаны в самом JSON.
- `docs/compute/hardware_trial_guide.md`, `docs/compute/hardware_signal.example.json`, extra `hardware_trial` в `pyproject.toml`.

## Проверки

- Целевой набор: **98 passed** (`test_hardware_trial`, `test_trial_transport`, `test_trial_ssh`, `test_trial_recovery`), полностью подменённые API/SSH/часы. Покрыты: отсутствие/испорченный сигнал, дубликаты source/episode, stale offer, цены/трафик, disk/deverified/excluded, отказ create, timeout после принятия, >6 пустых опросов, provisioning/probe/download/deadline failures, отказ destroy, чужой label/machine, сбой реестра/диска, поздний контракт, recover без create.
- Полный `python -m pytest`: **704 passed, 54 skipped** за 181.62 s; skips — отсутствие `reboundx` и Linux-only supervisor, не связаны с изменением. Лог: `pytest_full.txt`.
- `ruff check src scripts tests` — passed; LSP изменённых файлов — без диагностик.
- `prepare` выполнен фактически: `data/interim/vast_trials/prepared/20261023T102157Z-a86b5bc0.json`, сеть/аренда не использованы.
- Первый живой `run` по новому сигналу/эпизоду радара 16 корректно завершился **до create** с `offer_unavailable` (rentable=false), `create_requests=0`, инстанса нет. Это проверка fail-closed маршрута, а не аппаратного теста.

## Ограничения

- Полный успешный REST→running→SSH→probe→destroy путь на реальном инстансе **не пройден**. API/образ могут измениться; первый полный живой контроль возможен только со следующим новым пользовательским сигналом.
- Скорость «команда → create» не измерена; `runner_precreate_seconds` покрывает только внутренний участок исполнителя.
- OS-lock локален для workspace. Защита от нескольких клонов/компьютеров — операционная; обходить claims нельзя.
- Нет независимого watchdog. Выключение ПК/гибель контроллера требует `recover` или ручного вмешательства; JSON-бюджет не гарантирует прекращение платежей.
- Probe не проверяет всю RAM/VRAM, эксклюзивность хоста или контрактную дисковую квоту. 1% допуск сравнения RAM — операционный, не научная неопределённость.
