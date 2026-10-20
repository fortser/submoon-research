# Задание оператору: P2-2a (W2-P006) на одной станции Vast.ai — сценарий `p2_2a`

Ты — LLM-агент (Codex CLI или Pi) на компьютере пользователя (Windows 10, папка проекта
`T:\test_python\submoon-research`, WSL Ubuntu-24.04), с глобальным Python Windows с `vastai` и `paramiko`
и SSH-ключом `~/.ssh/vast_agent`. Прочитай документ полностью до любых действий.

Версия 1 (2026-10-14, claude-opus-20261014, задача W2-T019). Научная часть — `docs/tasks/W2_P2-2_plan_v1.md`
(W2-P006, поправка W2-D026); выбор станции и пределы — `docs/compute/W2_P2-2a_rental_plan_v2.md`
(поправка W2-T019 в начале документа). Старое задание P2-1 к этой аренде не относится.

## Что запускается и как устроено

- Сценарий `p2_2a` в `scripts/vast_p2.py` (поле `scenario` в approval). На сервере стартовый сценарий сам
  выполняет: установку → гейты (REBOUNDx импортируется, pytest стенда/приёмщика/порога, ruff) → проверку
  железа (физические ядра в пределах affinity, квота cgroup, RAM; меньше `min_workers` — `FAILED hardware`)
  → **базовый прогон** (4 ед./ячейку, D/T на 1 и 10 лет, B на 1 год, до 30 мин) и его строгую приёмку →
  **автоматическое решение** `scripts/baseline_gate_p2_2.py`: по факт/модель этой машины прогноз объёма и
  самых долгих заданий; не укладываются D/T до срока стенда с запасом ×1.15 — `FAILED baseline`, полный
  стенд не запускается → полный стенд `admission_p2_2.py` (9504 задания, самые долгие первыми) со сроком
  и стоп-файлом → приёмка на сервере `verify_p2_2_run.py --expect-full --expect-decision-ready`.
- Срок стенда — абсолютный: создание + `run_deadline_hours` (20.5 ч). К сроку стенд сам останавливает
  незавершённые задания и пишет итог. По W2-D026 остановленные сроком B на 1000 лет решение D–T не
  блокируют. Сервер после итога продолжает оплачиваться, пока ты не выполнишь `finish` (выгрузка + удаление).
- Твой компьютер может быть выключен ночью: сервер работает сам. Поэтому **запуск — вечером**, а `finish` —
  на следующий день, когда этап станет `done` (ожидается 17–20.5 ч от создания), и не позже 22 ч от создания.

## 0. Правила

- Деньги — только в пределах `scratch/p2_rental/approval.json` с `approved: true` и дословной цитатой
  пользователя. Без неё ничего платного не делать. Одна аренда (`max_rent_attempts: 1`); замена машины,
  повтор после `FAILED`, рост ставки, срока или бюджета — только с новым разрешением пользователя.
- Секреты не печатать, не копировать на сервер и в файлы.
- Научный код и критерии не менять. Транспорт `scripts/vast_p2.py` — только минимальные исправления с
  записью в журнал. Гейты и порог на сервере не обходить.
- Файлы не удалять: ненужное переносить в `scratch/p2_rental/superseded/…`.
- Журнал: `python scripts/project.py track add --stage W2 --input <json> --actor <твой actor>
  --expected-revision <N>`; actor вида `codex-p2-2a-<дата>` / `pi-p2-2a-<дата>`.
- Команды аренды — глобальным Python Windows из корня проекта: `python -X utf8 scripts/vast_p2.py <action>`.

## 1. Подготовка (без затрат)

1. Проверка кода: дважды щёлкнуть `run_W2-T019_checks.bat` (или запустить его) и прочитать лог
   `scratch/claude/W2-T019_checks_*.log`. Ожидается: Windows-тесты Vast/транспорта passed; в WSL тесты стенда,
   приёмщика и порога passed (кроме известного таймаута W2-I009), ruff и validate OK. Иначе — **не
   арендовать**, сообщить пользователю.
2. Архив прошлой аренды (перенос): создать `scratch/p2_rental/superseded/W2-P2-1-55404980/` и перенести туда
   из `scratch/p2_rental/`: `approval.json`, `rental.json`, `attempts.json`, `offers.json`, `bundle.tar.gz`,
   `bundle_manifest.json`, `bundle_info.json`, `start_55404980.sh`, `seed_jobs.jsonl`, `seed_info.json`,
   `_offer_live.json`. `results_*.tar.gz` и `remote_logs_*` оставить на месте.
3. Approval — только после явного согласия пользователя на аренду: скопировать
   `scratch/p2_rental/approval_p2_2a_draft.json` в `scratch/p2_rental/approval.json`, выставить
   `approved: true`, `user_message` — дословная цитата пользователя, `approved_utc`. Остальные поля не менять.
4. `python -X utf8 scripts/vast_p2.py preflight` — `cli`, `ssh_key`, `paramiko` true,
   `existing_live_rental` false, `enough_credit` true.
5. `python -X utf8 scripts/vast_p2.py prepare` — новый пакет; записать SHA из `bundle_info.json`.
6. `python -X utf8 scripts/vast_p2.py offers` — свежий бесплатный поиск под профиль `p2_2a_station` с
   пределами approval. Первое предложение — то, что будет арендовано. Пусто или первое не из разрешённого
   (CPU, ставка ≤ $0.40/ч с диском, ≥ 64 доступных vCPU, verified/deverified) — **не расширять** фильтр,
   сообщить пользователю ближайшие варианты.

## 2. Аренда и запуск (вечер; платно)

1. `rent` — перед create бесплатная перепроверка машины; занятая пропускается. Записать instance_id.
2. `wait` — до `running` (до 40 мин, иначе удаление автоматически).
3. `deploy` — загрузка пакета, сверка SHA, запуск. Записать в журнал задачу исполнения (instance_id, offer,
   ставка, срок стенда `run_deadline_utc`) и запись в `tracking/runtime.json` по протоколу.
4. Через 40–70 мин `status`:
   - `running … workers=N …` и в `baseline_gate` — `"verdict": "GO"`: всё штатно. Записать в журнал
     проверку: workers, физические ядра, факт/модель D/T/B, прогноз (`projected_hours`), предупреждения.
     Сообщить пользователю одной строкой прогноз окончания. Компьютер можно выключать.
   - `FAILED <этап>` (pytest, ruff, reboundx, hardware, baseline-accept, baseline) — `finish --partial`
     (выгрузка диагностики и удаление), запись issue с причиной, сообщить пользователю. Повторной аренды нет.
   - `setup`/`gates`/`baseline` дольше 90 мин от создания — `wrapup`, затем `finish --partial`; issue.

## 3. Завершение (на следующий день)

1. `status`. Этап `done exit=… accept=…` — `finish` (выгрузка со сверкой SHA каждого файла, destroy,
   проверка удаления). Если этап ещё `running`, а с создания прошло ≥ 21.5 ч — `wrapup`, через 3–5 мин
   `status` → `done`, затем `finish`; нет `done` за 15 мин — `finish --partial`.
2. Строгая приёмка выгруженного run в WSL (REBOUNDx есть только там):
   `wsl -d Ubuntu-24.04 --cd "T:\test_python\submoon-research" -- bash -c "PYTHONUTF8=1 ~/venvs/submoon-w2/bin/python scripts/verify_p2_2_run.py --run runs/W2-P2-2a-<iid> --expect-full --expect-decision-ready --output runs/W2-P2-2a-<iid>/acceptance_local.json"`.
   Дополнительно с `--expect-complete` — для отчёта, сколько B не завершено (это не провал по W2-D026).
3. Журнал: результат (решение P2-2a из `analysis.json` → `decision`: verdict, n, discordant, cp_upper,
   notes; доля `unresolved:high_e_prolonged` на 1000 лет для W2-D022; стоимость факт/модель), проверка приёмки,
   итог расходов (оценка по `rental.json`, не окончательный счёт), очистка `tracking/runtime.json`.
4. Сообщить пользователю: решение, приёмка, часы и оценка расходов, удаление подтверждено.

## Отказы и действия

| Ситуация | Действие |
|---|---|
| `offers` пусто или вне пределов | Не арендовать; сообщить варианты |
| create не подтверждён | Не повторять; сообщить |
| `FAILED pytest/ruff/reboundx/hardware/baseline-accept` | `finish --partial`; issue; сообщить |
| `FAILED baseline` (не укладывается в срок) | `finish --partial`; issue с `baseline_gate.json`; сообщить |
| ≥ 21.5 ч от создания, нет `done` | `wrapup` → `finish` (или `--partial`) |
| Ошибка выгрузки | Инстанс НЕ удалён; повторить `finish`; при повторной ошибке — сообщить пользователю |
| Расход приближается к $12 | `wrapup` → `finish`; сообщить |

## Фразы для пользователя (вставить оператору)

- Вечер: «Выполни docs/tasks/W2_P2-2a_operator_task.md, разделы 0–2. Разрешение: <цитата пользователя>.»
- Следующий день: «Продолжи docs/tasks/W2_P2-2a_operator_task.md, раздел 3.»
