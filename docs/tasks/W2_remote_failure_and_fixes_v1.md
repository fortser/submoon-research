# W2-T003: разбор сбоя удалённого прогона и план правок (v1)

Дата: 2026-10-06. Тип: инфраструктурно-числовой план исправлений (не научный отчёт).
Статус: подготовлен, правки не внесены.
Связанные ID: `W2-T003`, `W2-D002`, `W2-D003`, `W2-R001`, `W2-I001`.
Связанные файлы: `scripts/vast_w2.py`, `scripts/w2_remote_preflight.py`,
`src/submoon_research/dynamics/native_ias15.py`,
`src/submoon_research/events/escape.py`, `tests/physics/test_w2_engines.py`,
`configs/experiments/W2_three_engines_v1.yaml`.

> ВАЖНО. Пользователь самостоятельно отключил и удалил арендованный сервер
> (`instance 54515060`). Новый запуск требует новой аренды и **нового явного
> разрешения** по `docs/compute_policy.md`. Правило «править локально →
> пересобрать bundle → загрузить заново» остаётся обязательным.

---

## 1. Что произошло (проверенные факты)

- Первый deploy: 17:35 UTC, снимок `80887136 B`; preflight падал по SHA
  (`snapshot_sha256: false`, виноваты сгенерированные `src/submoon_research.egg-info/*`).
- Повторный deploy: 17:57 UTC, снимок `80888878 B` (`remote_pid 1862`,
  `rental.json: deployed_utc 17:57:49`); preflight прошёл (`snapshot_sha256: true`).
- Удалённый `/workspace/w2_execution.log` (3347 строк) оборвался на pytest:
  `83 failed, 144 passed, 2 warnings, 16 errors in 19.78s`.
- Скрипт `/workspace/w2_start.sh` содержит `set -euo pipefail`, поэтому после
  падения pytest **научная серия не запускалась вообще**: шаги
  `ruff / project validate / smoke / run_w2_comparison.py campaign` не выполнялись.
- На момент проверки удалённых процессов `run_w2_comparison.py` не было —
  научный бюджет `3600 s` израсходован на 0%.
- В `scratch/w2_remote/` за время разбора **другой сессией** менялись
  `bundle.tar.gz` (17:53 UTC), `rental.json` (17:57 UTC),
  `scripts/w2_remote_preflight.py` (17:43 UTC). Это признак параллельной работы
  другой harness-сессии над той же задачей — нужно развести владельцев файлов.

---

## 2. Корневые причины

| ID | Причина | Проявление | Доказательство |
|---|---|---|---|
| R1 | `package()` не включает `data/interim` | 16 errors: `FileNotFoundError .../data/interim/W0-himalia-v1/archinal2018.txt` | `bundle_manifest.json`: `data/interim in manifest = 0`; в bundle только `data/raw`, `data/processed` |
| R2 | `package()` не включает `good_cpu.txt` (фильтр top-level только `.md/.toml`) | 83 failed в `test_vast_market_monitor.py` (фикстура `targets`, строка 19) | `good_cpu.txt in_bundle = False` |
| R3 | `native_ias15.py:63` вызывает `self.sim.step()`, которого нет в REBOUND 5.1.1 | На сервере (есть `reboundx`) движок B падает: `AttributeError: 'Simulation' object has no attribute 'step'`. Локально `reboundx` не установлен → 9 тестов движка B **пропускаются**, баг маскируется | remote traceback `native_ias15.py:63`; локально `hasattr(rebound.Simulation(),'step') == False`, есть `steps`, `integrate` |
| R4a | `test_compiled_forces_against_independent_vectorized_and_potential`: J2-градиент, rtol `1e-8`, факт `4.30e-8` | 1 failed локально | `tests/physics/test_w2_engines.py:54`, `Max relative difference 4.297e-08` |
| R4b | `EscapeTracker.advance`: возврат ровно на границе окна не отменяет уход | 1 failed локально: возвращается `operational_escape` вместо `None` | `tests/physics/test_w2_engines.py:91`, `src/submoon_research/events/escape.py:82-97` |
| R5 | Удалённый гейт запускает **весь** pytest, включая инфраструктурные тесты, и `set -e` обрывает всё до науки | Любой нерелевантный фейл блокирует campaign | `/workspace/w2_start.sh`, `scripts/vast_w2.py:265-275` |
| R6 | Нет координации параллельных сессий на общих файлах (`vast_w2.py`, `scratch/w2_remote/*`) | Гонка пересборки/деплоя, противоречивые записи | mtime-наблюдения §1 |

---

## 3. План правок

### E0. Подготовка окружения (локально)

- [ ] Установить в локальный `.venv` `reboundx==5.1.0` (как на сервере), чтобы
      9 «skipped» тестов движка B реально исполнялись и ловили R3/R4 **до**
      платной аренды. Команда: `.venv/Scripts/python.exe -m pip install reboundx==5.1.0`.
- [ ] Проверить, что `pyproject.toml`/`requirements-w2.txt` фиксируют версии
      `rebound==5.1.1` и `reboundx==5.1.0` (локальный и удалённый наборы совпадают).

### E1. Полнота пакета — `scripts/vast_w2.py :: package()`

- [ ] Добавить в список деревьев `data/interim` (9.2 MB) рядом с
      `data/raw`, `data/processed`:
      ```python
      for name in ('src', 'scripts', 'tests', 'configs', 'docs', 'tracking',
                   'results/evidence', 'references',
                   'data/raw', 'data/processed', 'data/interim', 'runs'):
      ```
- [ ] Добавить `good_cpu.txt` (top-level `.txt` сейчас исключён фильтром
      `p.suffix in ('.md', '.toml')`). Вариант: явно `paths += [ROOT/'good_cpu.txt']`
      либо расширить набор суффиксов. Не добавлять лишние крупные `.txt`.
- [ ] Убедиться, что `bundle_manifest.json` содержит `data/interim/...` и
      `good_cpu.txt` (проверка после пересборки).
- [ ] Оценить прирост: bundle ≈ 80.9 MB → ≈ 90 MB (трафик в пределах $0.02/GB,
      бюджет не затрагивается).

### E2. Удалённый гейт — `scripts/vast_w2.py :: deploy()`

- [ ] Разделить гейты так, чтобы падение **нерелевантных** тестов было видно, но
      не блокировало науку незаметно; при этом научный гейт не ослаблять:
      - научный (обязательный, провал = стоп): `tests/physics`, `tests/integration`,
        `tests/contracts`;
      - инфраструктурный: `tests/unit/test_vast_market_monitor.py`,
        `test_vast_offer_classification.py` (после E1 должны проходить офлайн).
- [ ] Рекомендуемый компромисс: **оставить полный `pytest` как обязательный гейт**
      (качество), но после E1 он должен быть полностью зелёным. Если какой-то
      инфраструктурный тест окажется сетевым/CLI-зависимым, исключить его
      **явной причиной** через `--ignore` или маркер, не через ослабление порогов.
- [ ] Сохранить `set -euo pipefail`: campaign не должен стартовать при провале гейта.
- [ ] Зафиксировать в журнале исполнения порядок и границы гейтов.

### E3. Движок B (IAS15/REBOUNDx) — `src/submoon_research/dynamics/native_ias15.py`

- [ ] Заменить `self.sim.step()` (строка ~63) на поддерживаемый API REBOUND 5.1.1:
      `self.sim.steps(1)` — ровно один шаг с текущим `sim.dt`, что сохраняет
      семантику dense ABI (`a0`, `br` последнего выполненного шага).
- [ ] Перепроверить проверку ABI (`field_descriptor_list`) и чтение `a0`/`br`
      после `steps(1)`.
- [ ] Прогнать 9 ранее пропущенных тестов `tests/physics/test_w2_engines.py`
      (IAS15 dense endpoints/internal points, REBOUNDx J2 и т.д.).

### E4. Тест J2-градиента — `tests/physics/test_w2_engines.py`

- [ ] Определить, что авторитетно: аналитический `j2_vector` или конечная разность
      в тесте. Ошибка `4.3e-8` при `rtol 1e-8` характерна для погрешности
      конечной разности, но это **надо доказать**, а не предположить.
- [ ] Проверить шаг `h` и порядок аппроксимации; при подтверждении — исправить
      способ вычисления градиента (например, оптимальный центральный шаг),
      а не «подгонять» допуск.
- [ ] **Не ослаблять порог** без численного обоснования (правило AGENTS:
      пороги не меняются после просмотра исходов).

### E5. Граница окна ухода — `src/submoon_research/events/escape.py`

- [ ] Исправить `EscapeTracker.advance` так, чтобы возврат **на/до** момента
      `pending_since + window_seconds` отменял уход (тест
      `test_return_exactly_at_escape_window_cancels_departure`).
- [ ] Согласовать строгие/нестрогие неравенства: сейчас в цикле
      `pending_since + window < time`, в конце `pending_since + window <= t1`;
      при одновременных переходах должен действовать приоритет возврата.
- [ ] Перепроверить семантику `temporary_exits`/`returns` и `outcome()`.

### E6. Preflight — `scripts/w2_remote_preflight.py`

- [ ] Пропуск сгенерированных `src/submoon_research.egg-info/*` уже добавлен
      (`snapshot_failures: []` на повторном deploy) — проверить, что это
      зафиксировано в коммите и в bundle (SHA совпадает).
- [ ] Убедиться, что после E1 новые файлы (`data/interim`, `good_cpu.txt`)
      проходят SHA-проверку preflight.

### E7. Координация и провенанс

- [ ] Взять `W2-T003` в active с уникальным owner; при конфликте revision —
      перечитать изменения (`docs/tracking_protocol.md`).
- [ ] Развести редактируемые файлы между исполнителями; зафиксировать список
      файлов, которые правит только этот owner (`scripts/vast_w2.py`,
      `scripts/w2_remote_preflight.py`, `src/submoon_research/dynamics/native_ias15.py`,
      `src/submoon_research/events/escape.py`, `tests/physics/test_w2_engines.py`).
- [ ] Зафиксировать в git изменения кода/тестов/конфигов **до** сборки bundle,
      чтобы SHA снимка соответствовал версии в истории.

### E8. Уборка состояния аренды (сервер уже удалён пользователем)

- [ ] Остановить локальный watchdog (pid `16520`, всё ещё жив): при дедлайне он
      будет пытаться `finish()` для удалённого instance и зациклится на retry.
- [ ] Пометить запись аренды как завершённую по факту удаления пользователем
      (не `deployed`); обновить `tracking/runtime.json` (убрать процесс).
- [ ] Зафиксировать итог расходов (≈ 0.8 ч × $0.1656 ≈ **$0.13**) и отсутствие
      научных результатов.

---

## 4. Порядок исполнения

1. E0 → E3, E4, E5 (локальные правки и прогон тестов, пока платных ресурсов нет).
2. E1, E2, E6 (пакет/гейт/preflight) — локально.
3. `python scripts/vast_w2.py package` → проверить новый `bundle_info.json`
   и наличие `data/interim`, `good_cpu.txt` в манифесте.
4. E8 (уборка watchdog/состояния).
5. E7 (трекинг, owner, коммит).
6. Только после этого — **новое согласование аренды** и `deploy`.

---

## 5. Критерии приёмки правок (до новой аренды)

- [ ] `.venv/Scripts/python.exe -m pytest` проходит **полностью**, включая
      9 тестов движка B (с установленным `reboundx`).
- [ ] `ruff check src scripts tests` — без ошибок.
- [ ] `python scripts/project.py validate` — passed.
- [ ] `python scripts/project.py smoke` — passed (L0; не закрывает W2).
- [ ] В `bundle_manifest.json`: `data/interim/*` присутствует (≥1 файл),
      `good_cpu.txt` присутствует; SHA каждого файла совпадает локально.
- [ ] Новый `bundle.tar.gz` пересобран; SHA зафиксирован.
- [ ] Локальный прогон точной удалённой команды pytest даёт 0 failed.

---

## 6. Повторный запуск и бюджет (после правок)

- [ ] Новая аренда оформляется отдельным планом и **явным разрешением**
      пользователя (`docs/compute_policy.md`). Текущее разрешение W2-D002
      исчерпано вместе с удалением instance.
- [ ] Пересмотреть лимиты: 3 ч / $3 — это прежняя авторизация, не научная
      граница. 12–15 ч × ~$0.166 ≈ $2.0–2.5; но расширение требует нового
      согласования и **определённой научной цели** (длинные горизонты,
      измеренная стоимость шага для экстраполяции пилота T=10000 лет,
      масштабирование), иначе это не улучшит проверку.
- [ ] Порядок: preflight (SHA, quota, RAM, диск) → полный pytest → ruff →
      validate → smoke → `run_w2_comparison.py campaign --seconds …` →
      `download` (SHA-проверка) → `finish` (destroy).
- [ ] До `destroy` проверить полноту/хеши выгрузки; существующие `runs/`
      не перезаписывать.

---

## 7. Что явно НЕ делать

- Не править файлы на сервере для научного прогона (ломает SHA-провенанс и
  исчезает при redeploy).
- Не ослаблять научные пороги (`position_a`, `velocity_na`, `massive_energy`,
  `event_time_period`, `interpolation_state`, `meaningful_speedup`).
- Не считать `144 passed`/короткие тесты доказательством длительной динамики
  или допуском W1/W3.
- Не продлевать аренду/менять `max_hours` без нового явного разрешения.

---

## 8. Приложение: команды диагностики (без изменения сервера)

```bash
# локально
.venv/Scripts/python.exe -m pytest tests/physics/test_w2_engines.py -q
.venv/Scripts/python.exe -m pytest tests/unit/test_vast_market_monitor.py -q
tar -tzf scratch/w2_remote/bundle.tar.gz | grep -c data/interim      # сейчас 0
python - <<'PY'
import json; m=json.load(open('scratch/w2_remote/bundle_manifest.json',encoding='utf-8'))['files']
print('good_cpu.txt' in m, sum(k.startswith('data/interim') for k in m))
PY
```

```python
# проверка API REBOUND 5.1.1
import rebound
s = rebound.Simulation(); s.add(m=1.0, x=1.0); s.integrator = 'ias15'
print(hasattr(s, 'step'), hasattr(s, 'steps'), hasattr(s, 'integrate'))  # False True True
```

---

## 9. Открытый следующий шаг

Правки E0–E6 внести локально, прогнать критерии §5, затем решить с
пользователем вопрос новой аренды (§6) и только после этого выполнять
`package`/`deploy`. Все изменения зарегистрировать через
`scripts/project.py track add` с фактическим revision.
