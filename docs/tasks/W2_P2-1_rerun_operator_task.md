# Задание оператору: перезапуск W2-P004 P2-1 на Vast.ai после W2-I017 (решение W2-D021)

Ты — LLM-агент (Codex CLI или Pi) с доступом к компьютеру пользователя (Windows 10, папка проекта
`T:\test_python\submoon-research`, WSL Ubuntu-24.04), к глобальному Python с `vastai` и `paramiko`,
к SSH-ключу `~/.ssh/vast_agent`. Прочитай документ полностью до любых действий. Он заменяет для этой
аренды `docs/tasks/W2_P2-1_remote_operator_task.md` (версия 2); всё, что здесь не оговорено, — как там.

Версия 3 (2026-10-11, claude-opus-20261011).

## Что изменилось и зачем

- **W2-I017 (fixed):** страж IAS15 (движок B) собирает полином шага на `sim.dt_last_done`; прежние
  4 отказа B на 1000 лет (ganymede-inner) были артефактом округления времени. Нужен новый run B до
  1000 лет. Отпечаток реализации B изменился — все B-задания считаются заново автоматически.
- **W2-D021 (accepted):** новый run `W2-P2-1-<iid>` (supersedes `W2-P2-1-55200007`):
  - **seed:** 96 завершённых строк D/T 10000 лет переносятся из 55200007 (их код не менялся). Стенд
    сам примет только строки с совпавшим `job_fingerprint`; всё остальное (обязательный набор
    1–1000 лет D/T/B, B 100 и 1000 лет) считается заново — это и проверка воспроизводимости D/T.
  - **только P-ядра:** на i9 (8 P + 16 E) длинные задания B на E-ядрах в 2–2.5 раза медленнее.
    `pcores_only`: по одному процессу на P-ядро (8 воркеров, `taskset`). Если P-ядра не
    определились — сценарий завершится `FAILED pcores` (не обходить).
- Ожидание: настройка ~0.5–1 ч; обязательный набор ~2–3 ч; самое длинное задание B
  (ganymede-inner, ~112 млн шагов) ~11–12 ч на P-ядре; итог **~15–19 ч от создания**.
  Срок стенда 22.5 ч от создания, предел аренды 24 ч, бюджет $5 при ставке ≤ $0.20/ч.

## 0. Правила (как в версии 2, кратко)

- Деньги — только в пределах `scratch/p2_rental/approval.json` (`approved: true` и цитата пользователя).
  Без неё ничего платного не делать.
- Секреты не печатать, не копировать на сервер и в файлы.
- Научный код и критерии не менять. Транспорт `scripts/vast_p2.py` — только минимальные исправления
  с записью в журнал. Научный гейт на сервере не обходить.
- **Файлы не удалять** (у агента Claude в этой папке нет права удаления; у тебя — не использовать):
  ненужное **переносить** в `scratch/p2_rental/superseded/…` или `scratch/claude/_to_delete/`.
- Журнал: `python scripts/project.py track add --stage W2 --input <json> --actor <твой actor>
  --expected-revision <N>`; actor вида `codex-p2rerun-20261011` / `pi-p2rerun-20261011`.
- Команды аренды — глобальным Python Windows из корня проекта: `python -X utf8 scripts/vast_p2.py <action>`.

## 1. Подготовка (без затрат)

1. Проверка кода в WSL (~5 мин): `run_W2-I017_checks_WSL.bat` или
   `wsl -d Ubuntu-24.04 --cd "T:\test_python\submoon-research" -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py i017"`.
   Ожидается: pytest — всё, кроме известного таймаута W2-I009 (`test_l1_acceptance::…canonical_control…`);
   ruff OK; validate OK; `bench_quick` (диагностика dt_last_done) OK; `stand_quick` OK.
   Любая другая ошибка — **не арендовать**, сообщить пользователю.
2. Архив прошлой аренды (перенос, не удаление): создать `scratch/p2_rental/superseded/W2-P2-1-55200007/`
   и перенести туда из `scratch/p2_rental/`: `approval.json`, `rental.json`, `attempts.json`,
   `offers.json`, `bundle.tar.gz`, `bundle_manifest.json`, `bundle_info.json`, `start_55200007.sh`,
   `cost_eta.json`, `jobs_current.jsonl` (что есть). `results_55200007.tar.gz` и `remote_logs_55200007/`
   оставить на месте.
3. Approval: только после явного согласия пользователя на аренду. Скопировать
   `scratch/p2_rental/approval_rerun_W2-D021_draft.json` в `scratch/p2_rental/approval.json`,
   выставить `approved: true`, `user_message` — дословная цитата пользователя, `approved_utc`.
   (Если approval уже подготовлен Claude — проверить эти поля и не менять.)
4. `python -X utf8 scripts/vast_p2.py preflight` — `cli`, `ssh_key`, `paramiko` true,
   `existing_live_rental` false, `enough_credit` true.
5. `python -X utf8 scripts/vast_p2.py seed` — ожидается `rows: 96`,
   `source_sha256: bb4ed301…3645`. Иначе — остановиться и сообщить.
6. `python -X utf8 scripts/vast_p2.py offers` — i9-13900K/14900K(F), ≤ $0.20/ч. Пусто — не расширять
   фильтр, сообщить пользователю ближайшие варианты.
7. `python -X utf8 scripts/vast_p2.py prepare` — после шага 1 (в пакете новый код).
8. Журнал W2: задача `W2-T011` (task, active, owner = твой actor) «Перезапуск P2-1 после W2-I017
   (W2-D021)», refs: это задание, лог проверки шага 1, `scratch/p2_rental/seed_info.json`;
   related: W2-D021, W2-I017, W2-T008, W2-P004; next_action — аренда.

## 2. Аренда и запуск (платно)

1. `rent` → `wait` → `deploy` (как в версии 2). `deploy` сам загружает seed в
   `runs/W2-P2-1-<iid>/jobs.jsonl` на сервере и сверяет его SHA; печатает `run_deadline_utc`.
2. `tracking/runtime.json`: run_id `W2-P2-1-<iid>`, instance_id, создан, `run_deadline_utc`,
   предел 24 ч / $5, команда остановки `finish`, лог.
3. `status` каждые 5–10 мин до этапа `running`. Проверить в строке этапа и в хвосте лога:
   - `pin=<8 номеров через запятую>` (до 2026-10-12: `pin=taskset -c …`) и `workers=8` (на i9-13900K/14900K — 8 P-ядер);
   - `seed_rows=96`;
   - строка стенда `уже готово заданий 96 (отброшено несовпавших по отпечатку 0)`;
   - строки `diag_ias15_guard_dtdone`: `исправление … OK`.
   **Если готово меньше 96 или отброшено > 0** (окружение сервера дало другой отпечаток D/T) —
   D/T 10000 лет пришлось бы считать заново, срок не уложится: сразу `wrapup`, затем `finish --partial`,
   записать issue, сообщить пользователю. Если `FAILED pcores` — `finish --partial`, issue, сообщить.
4. Обновить W2-T011: инстанс, CPU, ставка, workers/pin, seed принят (96/0), `run_deadline_utc`.
5. Сообщить пользователю: всё запущено, ожидаемое окончание (создание + 15–19 ч), компьютер можно
   выключить; потом запустить тебя снова с этим заданием (раздел 3).

## 3. Контроль и завершение

1. `status`. Ориентиры по `progress.json` (от создания): через ~3–4 ч обязательный набор завершён;
   через ~10–12 ч остаются в основном длинные B 1000 лет (ganymede/himalia/iapetus-inner).
2. `done exit=0|1` — `finish` (выгрузка со сверкой SHA каждого файла, `destroy`, проверка удаления).
   Ещё `running`: ждать до `run_deadline_utc` (стенд сам остановит незавершённое как partial и подведёт
   итог). Если пользователь просит раньше или прошло ≥ 23 ч — `wrapup`, затем `finish`;
   нет `done` за 15 мин — `finish --partial`.
3. Evidence в `results/evidence/W2_P2-1_remote_<iid>/` (как в версии 2) + `seed_info.json`,
   `diag_ias15_guard_dtdone.json`. Проверить: в выгруженном `jobs.jsonl` все 96 seed-строк на месте
   и 576 новых (всего 672).
4. Отчёт `reports/operations/W2_P2-1_rerun_<YYYYMMDD>.md` (как в версии 2) — отдельно указать: seed
   принят 96/96, pin/workers, длительность самого долгого задания B. Научную интерпретацию не писать.
5. Журнал: W2-T011 → done; check выгрузки/удаления; runtime.json — снять процесс; `validate`.
6. Сообщить пользователю итог по ступеням, расход, где данные. Разбор — Claude (W2-T008:
   сравнение D/T 1–1000 лет с 55200007, B 1000 лет, verified для W2-I017).

## 4. Сбои

| Ситуация | Действие |
|---|---|
| Шаг 1 не прошёл (кроме W2-I009) | Не арендовать; сообщить |
| `seed`: не 96 строк или SHA источника другой | Не арендовать; сообщить |
| Нет i9-13900K/14900K ≤ $0.20/ч | Не расширять фильтр; сообщить варианты |
| `FAILED pcores` | `finish --partial`; issue; сообщить |
| Seed принят не полностью (готово < 96 или отброшено > 0) | `wrapup` → `finish --partial`; issue; сообщить |
| `FAILED setup/apt/pip` | Повторная аренда в пределах 2 попыток |
| `FAILED pytest/ruff/quick` | Не обходить: `finish --partial` / `destroy`; issue; сообщить |
| Расход к $5 или 23 ч | `wrapup` → `finish` (или `--partial`); сообщить |
