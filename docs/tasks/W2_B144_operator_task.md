# Задание оператору: B144 (W2-P014 шаг 4) на одной станции Vast.ai — сценарий `b144`

Ты — LLM-агент (Codex CLI или Pi) на компьютере пользователя (Windows 10, папка проекта
`T:\test_python\submoon-research`, WSL Ubuntu-24.04), с глобальным Python Windows с `vastai` и `paramiko`
и SSH-ключом `~/.ssh/vast_agent`. Прочитай документ полностью до любых действий.

Версия 1 (2026-10-21, claude-opus-20261021-t050, задача W2-T050). Научная часть и пределы —
`docs/compute/W2_B144_rental_plan_v1.md`; правило вывода — docstring `scripts/b144_regress.py`.
Задания P2-1/P2-2a к этой аренде не относятся.

## Что запускается

- Сценарий `b144` в `scripts/vast_p2.py` (поле `scenario` в approval). На сервере стартовый сценарий сам:
  установка → гейты (REBOUNDx, pytest, ruff) → железо и AVX-512 → **точные архивные старты** (`prepare`;
  любое расхождение — `FAILED inputs`, расчёта нет) → **замер скорости** с решением GO/NO-GO (`benchmark`) →
  один прогон 288 заданий (`run`) с абсолютным сроком и стоп-файлом → `done`.
- Срок расчёта — создание + `run_deadline_hours` (6.5 ч); к сроку незавершённые задания становятся `partial`.
  Сервер оплачивается, пока ты не выполнишь `finish`. Ожидаемая длительность всего — ~5 ч.
- **Дневной режим**: начинать утром, когда пользователь у компьютера; ПК не должен засыпать до `finish`.

## 0. Правила

- Деньги — только в пределах `scratch/p2_rental/approval.json` с `approved: true` и дословной цитатой
  пользователя. Одна аренда (`max_rent_attempts: 1`); замена машины, повтор после `FAILED`, рост ставки,
  срока или бюджета — только с новым разрешением пользователя.
- Личную станцию пользователя **instance 56229721 не трогать** (W2-D035): ни stop, ни destroy, ни команды.
- Секреты не печатать, не копировать на сервер и в файлы.
- Научный код, правило вывода и гейты не менять и не обходить. Транспорт `scripts/vast_p2.py` — только
  минимальные исправления с записью в журнал и сообщением пользователю.
- Файлы не удалять: ненужное переносить в `scratch/p2_rental/superseded/…`.
- Журнал: `python scripts/project.py track add --stage W2 --input <json> --actor <твой actor>
  --expected-revision <N>`; actor вида `pi-b144-<дата>` / `codex-b144-<дата>`.
- Команды аренды — глобальным Python Windows из корня проекта: `python -X utf8 scripts/vast_p2.py <action>`.

## 1. Подготовка (без затрат)

1. Проверка кода: запустить `run_W2-T050_checks.bat`, прочитать `scratch/claude/W2-T050_checks_20261021.log`.
   Ожидается: все pytest passed (кроме явно пропущенных REBOUNDx-тестов на Windows), ruff и validate чисто,
   `devcheck` — `"verdict": "PASSED"` на 2 заданиях, сверка идентичностей на AVX2 — расхождения только у Япета
   (48 единиц × 2 горизонта = 96 заданий). Иначе — **не арендовать**, сообщить пользователю.
2. Архив прошлой попытки: создать `scratch/p2_rental/superseded/W2-P2-2a-55911074/` и перенести туда из
   `scratch/p2_rental/`: `approval.json`, `rental.json`, `attempts.json`, `offers.json`, `bundle.tar.gz`,
   `bundle_info.json`, `bundle_manifest.json`. Без этого `rent` откажет (лимит попыток исчерпан).
3. Approval — только после явного согласия пользователя на аренду: скопировать
   `docs/compute/W2_B144_approval_draft.json` в `scratch/p2_rental/approval.json`, выставить `approved: true`,
   `user_message` — дословная цитата пользователя, `approved_utc`. Остальные поля не менять.
4. `python -X utf8 scripts/vast_p2.py preflight` — `cli`, `ssh_key`, `paramiko` true,
   `existing_live_rental` false, `enough_credit` true.
5. `python -X utf8 scripts/vast_p2.py prepare` — новый пакет; записать SHA из `bundle_info.json`.
6. `python -X utf8 scripts/vast_p2.py offers` — бесплатный поиск с пределами approval. Первое предложение —
   то, что будет арендовано. Пусто или первое вне пределов (CPU из `cpu_name_regex`, ставка ≤ $0.70/ч с диском,
   ≥ 32 доступных vCPU) — **не расширять** фильтр, сообщить пользователю ближайшие варианты. Если в списке
   есть machine 143224 (TR PRO 9975WX), предложить пользователю её (W2-D034) через `rent --offer-id`.

## 2. Аренда и запуск (утро; платно)

1. `rent` (или `rent --offer-id <id>`). Записать instance_id.
2. `wait` — до `running` (до 40 мин, иначе удаление автоматически).
3. `deploy` — загрузка пакета, сверка SHA, запуск. Записать в журнал задачу исполнения (instance_id, offer,
   ставка, `run_deadline_utc`) и запись в `tracking/runtime.json` по протоколу.
4. `status` через 15–25 мин и далее по этапам:
   - `inputs …` → затем `identity` в выводе `status`: `"passed": true, "checked": 288` — штатно.
   - `FAILED inputs` — точные старты не воспроизведены: `finish --partial`, issue с `identity.json`, сообщить
     пользователю. Повторной аренды нет.
   - `benchmark …` → `running …`: в `benchmark` — `"verdict": "GO"`, прогноз `predicted_wall_s`. Записать в
     журнал проверку: workers, `manifest=<SHA>` из строки этапа, отношение скоростей, прогноз окончания.
     Сообщить пользователю одной строкой прогноз.
   - `FAILED <этап>` (reboundx, pytest, ruff, hardware, avx512, benchmark) — `finish --partial`, issue с
     причиной, сообщить пользователю.
   - `setup`/`gates`/`inputs` дольше 60 мин от создания — `finish --partial`; issue.
5. Далее `status` каждые 30–60 мин (`progress`: done/planned, counts). ПК не выключать и не усыплять.

## 3. Завершение

1. Этап `done exit=…` — `finish` (выгрузка со сверкой SHA каждого файла, destroy своего инстанса, проверка
   удаления). Если `running`, а с создания прошло ≥ 6.7 ч — `wrapup`, через 3–5 мин `status` → `done`, затем
   `finish`; нет `done` за 15 мин — `finish --partial`. Не позже 8 ч от создания.
2. Локальный анализ в WSL (REBOUNDx есть только там):
   `wsl -d Ubuntu-24.04 --cd "T:\test_python\submoon-research" -- bash -c "PYTHONUTF8=1 ~/venvs/submoon-w2/bin/python scripts/b144_regress.py analyze --run-dir runs/W2-B144-<iid> --manifest-sha256 <SHA manifest.json> --output results/evidence/W2_B144_<iid>/analysis.json"`.
   Сначала сверить, что SHA локального `runs/W2-B144-<iid>/manifest.json` равен записанному в журнал.
3. Журнал: результат (verdict, failed/partial, CP-граница, расхождения с B/D/T, диагностика, стоимость из
   `analysis.json`), итог расходов (оценка по `rental.json`, не окончательный счёт), очистка
   `tracking/runtime.json`.
4. Сообщить пользователю: вердикт, часы и оценка расходов, удаление подтверждено. Научный разбор — Claude.

## Отказы и действия

| Ситуация | Действие |
|---|---|
| Лог проверки не зелёный | Не арендовать; сообщить |
| `offers` пусто или вне пределов | Не арендовать; сообщить варианты |
| create не подтверждён | Не повторять; сообщить |
| `FAILED inputs` | `finish --partial`; issue с `identity.json`; сообщить (нужно новое решение) |
| `FAILED reboundx/pytest/ruff/hardware/avx512/benchmark` | `finish --partial`; issue; сообщить |
| ≥ 6.7 ч от создания, нет `done` | `wrapup` → `finish` (или `--partial`) |
| Ошибка выгрузки | Инстанс НЕ удалён; повторить `finish`; при повторной ошибке — сообщить |
| Оценка расходов приближается к $8 | `wrapup` → `finish`; сообщить |

## Фразы для пользователя (вставить оператору)

- Подготовка: «Выполни docs/tasks/W2_B144_operator_task.md, раздел 1 (без аренды).»
- Утро, после согласия: «Выполни docs/tasks/W2_B144_operator_task.md, разделы 2–3. Разрешение: <цитата пользователя>.»
