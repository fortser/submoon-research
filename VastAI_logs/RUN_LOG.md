# VastAI Log — трекинг ошибок и решений

Файл для фиксации ошибок, возникающих при работе с Vast.ai (CLI, SSH, аренда, передача данных),
и успешных способов их обхода. Ведётся хронологически. Каждая запись:
- дата/время, контекст
- ошибка (текст, код)
- принятое решение / успешное решение
- результат

---

## 2026-10-06 — сессия: проверка арендованного сервера

### [E1] vastai CLI: SSL-ошибка при запросе через прокси
- **Контекст**: `vastai show instances --raw` из корня проекта.
- **Ошибка**: `urllib3.exceptions.SSLError: EOF occurred in violation of protocol (_ssl.c:997)` /
  `MaxRetryError ... console.vast.ai:443`. Curl при этом работает.
- **Причина**: утилита `vastai` (pip, Python 3.10, OpenSSL 1.1.1n) не проходит TLS-handshake
  через прокси `127.0.0.1:10808` (переменные `HTTP_PROXY`/`HTTPS_PROXY`). Прокси жив
  (проверено: curl через него даёт HTTP 302), проблема именно в клиенте.
- **Решение**: вызывать vastai без прокси-переменных:
  `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai ... --raw`.
  Всё, что нужно для vastai (console.vast.ai), доступно напрямую, прокси не обязателен.
- **Результат**: работают `show instances`, `show instance`, `ssh-url`.

### [E2] SSH на сервер: прямое соединение недоступно, нужен прокси
- **Контекст**: IP сервера `201.132.16.122`, порт из инстанса 39450.
- **Ошибка**: `Connection timed out during banner exchange` / `Connection to ... timed out`
  при прямом `ssh root@ssh2.vast.ai -p 39450`.
- **Решение**: туннелировать SSH через SOCKS5-прокси `127.0.0.1:10808` с помощью
  `connect.exe` (поставляется с Git для Windows, `C:\Program Files\Git\mingw64\bin\connect.exe`):
  ```
  ssh -o ProxyCommand="connect -S 127.0.0.1:10808 %h %p" -i ~/.ssh/vast_agent \
      -p <SSH_PORT> root@<SSH_HOST/IP> "<команда>"
  ```
- **Результат**: соединение проходит (баннер OpenSSH_9.6p1 получен).

### [E3] SSH: порт из `show instance` (39450) не тот, что в ssh-url
- **Контекст**: `show instance` отдаёт `ssh_host=ssh2.vast.ai, ssh_port=39450`,
  но через него соединение закрывается (`Connection closed by UNKNOWN port 65535`).
- **Решение**: брать строку подключения из `vastai ssh-url <id> --raw`
  (даёт `ssh://root@<IP>:<порт>`). Актуально: `root@201.132.16.122:50049`.
- **Примечание**: `ssh2.vast.ai:39450` резолвится в другой IP (54.158.54.242) — не тот хост.

### [E4] SSH: Host key verification failed
- **Контекст**: при первом подключении `StrictHostKeyChecking=yes` (BatchMode).
- **Причина**: в `~/.ssh/known_hosts` не было ключа этого сервера (или ключ чужого хоста).
- **Решение**: `-o StrictHostKeyChecking=accept-new` однократно, ключ добавляется в known_hosts.
  После этого подключения проходят штатно.

### [E5] SSH: нестабильное соединение через прокси
- **Контекст**: первая попытка — успех (баннер), следующая — `Connection timed out during banner exchange`.
- **Решение**: повторять подключение в цикле (3 попытки с паузой 3 с). Успех на 2-й попытке.
  Это типичное поведение прокси-туннеля, не ошибка сервера.

### [E6] Мониторинг CPU: `top -bn2` в batch-режиме не раскрывает ядра
- **Контекст**: нужно было посмотреть загрузку по каждому ядру.
- **Ошибка**: `top -bn2` выдаёт только строку `%Cpu(s):` (агрегат) и список процессов,
  пер-ядерная разбивка появляется только в интерактивном режиме после нажатия `1`.
- **Решение**: снимать два снимка `/proc/stat` (пауза ~3 с) и считать разницу по полям 2–11
  (total и idle, `Использование = (total - idle)/total`). Кратко:
  ```python
  import time
  def snap(): ...
  # user = (b[0]-a[0])/dt, sys = (b[1]+b[2]+b[3]-...)/dt, idle = (b[4]-a[4])/dt
  ```

### [E7] Наследование heredoc/awk на сервере
- **Контекст**: передача многострочных awk/python-скриптов через ssh.
- **Ошибка**: скрипт с `paste -d"\n"` + process substitution (`<(...)`) упал без вывода —
  та же проблема была с f-string внутри heredoc (escaping `\"` ломает синтаксис).
- **Решение**: писать скрипт локально (файл в `temp_scripts/`), передавать на сервер через
  stdin: `ssh ... 'python3 - ' < script.py` или писать в /tmp на сервере отдельным heredoc
  с `<< "EOF"` (без подстановки). Успешный вариант — Python-скрипт через stdin.

---

## 2026-10-06 — сессия: поиск серверов (CLI vastai 0.3.1, Windows)

### [E8] `vastai search offers` зависает >90 с (таймаут) при пайпе в python
- **Контекст**: `vastai search offers 'gpu_name=RTX_4090 num_gpus=1 cpu_cores_effective>=16' --limit 30 --order dph --raw | python -c ...`
- **Ошибка**: вызов не вернулся за 90 с (kill по таймауту). Ранее в той же сессии такие же поиски без пайпа вернулись за секунды — причина нестабильна.
- **Причина**: вероятно, та же, что в [E1] — клиент vastai (pip, Python 3.10) неустойчив через прокси `127.0.0.1:10808`; флуктуация TLS/соединения.
- **Решение**: всегда вызывать vastai со снятыми прокси-переменными: `env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai search offers ...`; уменьшать `--limit`; в скриптах — retry-цикл (до 3 попыток), успех по отсутствию `failed with error|Warning|Traceback` (см. §9 памятки).
- **Результат**: повторный запуск с `env -u ...` и `--limit 10` вернул результат — отбор работает.

### [E9] `cpu_name` — НЕ поисковый ключ (в отличие от `cpu_cores`/`cpu_arch`/`cpu_ghz`)
- **Контекст**: вопрос пользователя — можно ли отбирать машины по типу/названию процессора. Проверка: `vastai search offers 'gpu_name=RTX_4090 cpu_name=EPYC'`.
- **Ошибка**: `Warning: Unrecognized field: cpu_name` + пустой результат `[]`. Памятка (§9) подтвердилась. `--explain` показал: в запрос к API попадают только `gpu_name`, `cpu_cores_effective` и т.п.
- **Решение (двухэтапный отбор)**: 1) сузить выдачу поисковыми полями: `gpu_name`, `num_gpus`, `cpu_arch=amd64|arm64`, `cpu_cores_effective>=N`, `cpu_ghz`; 2) докать `--raw` и фильтровать на клиенте по подстроке `cpu_name` (поле есть в JSON оффера, напр. `'AMD EPYC 7542 32-Core Processor'`).
- **Результат**: для `RTX_4090 num_gpus=1 cpu_cores_effective>=16` (30 дешёвых офферов): EPYC 21 шт, Ryzen 6, Core i9 2, Xeon 1. Стоимость и локация видны из JSON.

### [E10] `vastai search offers --explain` печатает api_key открытым текстом
- **Контекст**: отладка фильтров поиска.
- **Ошибка/внимание**: в выводе `--explain` полный URL `https://console.vast.ai/api/v0/bundles/?api_key=<СЕКРЕТ>`.
- **Решение**: не использовать `--explain` в автоматизированных/логгируемых скриптах; при выводе в лог маскировать ключ. Отозвать ключ, если он попал в общий вывод.

### [W1] UnicodeEncodeError cp1251 при печати кириллицы из python-пайпа
- **Контекст**: `python -c "...print(...)"` с кириллицей/символами `®` `ü` из JSON офферов на Windows-консоли.
- **Ошибка**: `UnicodeEncodeError: 'charmap' codec can't encode character '\xfc'` (cp1251).
- **Решение**: `export PYTHONIOENCODING=utf-8` перед python-пайпом (или писать вывод в UTF-8 файл, читать read-инструментом).
- **Результат**: вывод корректный, пайп не падает.

### [E8] Анализ /proc/stat: перепутаны индексы полей → «фантомная» загрузка 100%
- **Контекст**: расчёт загрузки ядер по разнице двух снимков `/proc/stat`.
- **Ошибка**: использованы индексы `[3]` как idle (по факту это system) и `[4]` как
  занятость (по факту это idle). Вместо idle брался iowait → при iowait≈0 весь dt
  считался «занятым», все ядра показывали 100% занятости при фактическом простое.
- **Правильная раскладка полей** (после имени cpuN):
  `0=user 1=nice 2=system 3=idle 4=iowait 5=irq 6=softirq 7=steal 8=guest 9=guest_nice`
  → user = `(b[0]-a[0])/dt`, sys = `(b[1]+b[2]-a[1]-a[2])/dt`, idle = `(b[3]-a[3])/dt`,
  занятость = `100 - idle*100/dt`.
- **Второй баг**: строка `print(`AVG ... %d cores:` % n)` с литеральным `% over` ломает
  `%`-форматирование — `TypeError: not enough arguments for format string`.
- **Решение**: использовать f-string/`.format()` и не смешивать `%s`-подстановку с
  литеральными `%`.
- **Результат (эталон)**: повторный замер 2 с по 8 полям на 32 ядрах:
  user=0.05%, sys=0.02%, idle=99.94%, iowait/irq/softirq/steal=0%.

---

## 2026-10-06 — результат: проверка CPU арендованного сервера (инстанс 54458725)

Контекст: RTX 4060, 1×GPU, хост shared (Vast.ai).

- CPU: Intel Core i9-14900KF, 32 логических ядра (24 физических, HT),
  `CPU max 6000 МГц, min 800 МГц`, NUMA node0: 0-31. Виртуализация: KVM (VT-x).
- Загрузка: load average 0.05 / 0.08 / 0.16; за окно 2 с по /proc/stat:
  idle 99.94%, user 0.05%, sys 0.02% — сервер практически простаивает.
- Частоты (мгновенный срез /proc/cpuinfo): большинство ядер 800 МГц (мин. частота),
  часть на 1.4–1.6 ГГц, отдельные всплески до 4.1–5.3 ГГц (ядра, «просыпающиеся»
  под нагрузку; Booster). Это нормально для idle-сервера с Intel SpeedStep.
- Вывод: сервер доступен и свободен, можно запускать расчёты, CPU-ресурс
  виртуальной машины (32 ядра) не конкурирует ни с чем.

### [E9] min()/max() по loadavg-строкам → лексикографическое сравнение
- **Контекст**: `min(loadavgs)`/`max(loadavgs)`, где элементы — строки `"10.10"`, `"8.91"`.
- **Ошибка**: сравнение строк дало `min > max` ("10.10" < "8.91" лексикографически).
- **Решение**: парсить в float до агрегации.

---

## 2026-10-06 — результат: нагрузочный тест CPU (инстанс 54458725)

Методика: 32 busy-process (Python float-цикл), сэмплирование `scaling_cur_freq`
из `/sys/devices/system/cpu/cpuN/cpufreq` + idle/steal из `/proc/stat` каждые 5 с,
длительность 300 с, 60 сэмплов на ядро. Скрипт: `temp_scripts/cpu_freq_load_test.py`.

Итог:
- N ядер = 32 (i9-14900KF: 8 P-ядер × HT = 16 логических + 16 E-ядер = 32).
- Загрузка: avg idle 4.1% (ядра заняты ~96%), steal 0% — соседи по хосту не вмешиваются.
- Частоты под нагрузкой (MHz):
  - P-ядра (cpu0–15): ~3300–4000, средняя ≈ 3470, пик 4000
  - E-ядра (cpu16–31): ~1460–1840, средняя ≈ 1570, пик 1840
- Вывод: Turbo Boost до паспортных 5.7/6.0 ГГц НЕ достигается (губернатор `powersave`,
  лимит хоста Vast.ai): реальная частота P-ядер ~3.5 ГГц, E-ядер ~1.6 ГГц.
  Для планирования расчётов закладывать эту фактическую производительность.
  Первые сэмплы cpu2/4/6/14 показывают 800 МГц — разгон после старта нагрузки.

### [E11] CPU-only станции (num_gpus=0) НЕ попадают в обычную выдачу search offers
- **Контекст**: задача «актуальные станции без учёта видеокарты». Обычный запрос `cpu_cores_effective>20 dph_total<=0.20` вернул 58 шт, все с GPU (num_gpus 1–2), хотя порог 0.20 и ядра >20 должны были пустить и CPU-only.
- **Проверка**: даже запрос с пустым условием `'' --limit 100` возвращает 100 офферов, ни одного с num_gpus==0. А отдельный `num_gpus=0` даёт десятки CPU-only (от 0.01 $/ч).
- **Решение**: для охвата «всех станций без учёта видеокарты» нужны ДВА запроса: обычный + `num_gpus=0`; результаты объединять и дедуплицировать по id.
- **Результат**: CPU-only запрос добавил 50 станций (дёшевые, 0.01–0.02 $/ч). Сохранено: `results/vast_stations_2026-10-06.txt` (топ-100 по цене, сортировка по убыванию).

### [E12] В выдаче search offers бывает cpu_name=None
- **Контекст**: фильтрация CPU-only офферов по названию процессора.
- **Ошибка**: `AttributeError: 'NoneType' object has no attribute 'replace'` у 2 из 87 офферов.
- **Решение**: защита `name = (o.get('cpu_name') or '').replace('™','')`.

### [N1] У CPU-only офферов cpu_ram=0 (RAM не специфицирован)
- **Контекст/наблюдение**: все CPU-only (num_gpus=0) офферы в выдаче имеют `cpu_ram=0` (в отличие от GPU-машин).
- **Действие**: перед арендой CPU-only проверять реальную память (show instance / вопрос продавцу) — для расчётов с большим потреблением RAM это критично.

### [N2] Снимок рынка по конкретным CPU (2026-10-06, ~12:45)
- EPYC 9555 (Zen 5): 1 шт (id=29519536, Испания, 0.0123 $/ч, 256 ядер).
- EPYC 9554 (Zen 4): 1 шт (id=47185115, Таиланд, 0.0114 $/ч, 64 ядра).
- EPYC 9654 (Zen 4): 9 шт (0.0107–0.0378 $/ч, 192–384 ядра; BC/CA, PL, AZ, RU-Ростов, HU×4, IN).
- Core i9-14900KF: 0 шт в актуальном срезе (был id=49724148, UK, 0.0106 $/ч в снимке ~12:40 — оффер ушёл). Ближайшие: i9-13900KF×2, i7-14700KF×1 (~0.011 $/ч).
- Рынок CPU-only живой: офферы уходят за минуты. Сохранено: `results/vast_cpu_target_2026-10-06.txt`.

### [E13] Рынок CPU-only (num_gpus=0) мал: при `--limit 400` вернулось 87 (дефолт) / 225 (--no-default)
- **Контекст**: поиск CPU-only с `--limit 400`.
- **Факт**: дефолтная выдача — 87 офферов, расширенная — 225; целевые CPU — штучные, офферы уходят за минуты.
- **Вывод**: для редких CPU-станций нужен МОНИТОРИНГ, а не разовые срезы.

### [N3] Создан монитор появления целевых станций `scripts/vast_monitor.py` (проверен 13:04)
- **Логика**: каждые N сек ищет `num_gpus=0` (при `--with-gpu` — и обычную выдачу, т.к. i9-14900KF арендовался с RTX 4060), фильтрует на клиенте по подстроке cpu_name [E9], лимиту dph_total и мин. cpu_cores_effective; при НОВОМ id (нет в state) — звук + ANSI-баннер + запись в `VastAI_logs/vast_monitor_alerts.log`.
- **Состояние**: `scratch/vast_monitor_state.json` (алерты не повторяются).
- **Устойчивость**: снимает прокси [E1], retry 3 × таймаут 60 с [E8], проверка `failed with error|Traceback` вместо exit code [§9].
- **Тест --once**: найдено 4 станции (EPYC 9555 Болгария 0.0113; EPYC 9654 ×3: Германия 0.0114, Венгрия ×2). ruff OK.
- **Правило проекта**: скрипт НЕ арендует автоматически — только сигнал; аренда после согласования плана/бюджета с пользователем.

### [N4] Создан трекер CPU-only станций `scripts/vast_track_cpu.py` (проверен 13:36)
- **Источник списка CPU**: `good_cpu.txt` (корень) — скрипт сам выводит 41 паттерн (i9/i7, Core Ultra, Ryzen 5/7/9, Threadripper/TR PRO, Xeon W-24/W-34, EPYC F-серия/75F3/73F3/7543/7443/7343/EPYC 4-серия/9555/9554/9654).
- **Условия отбора**: без видеокарт (num_gpus=0), не арендована, диск >= 4 GiB, **эффективные ядра == физические** (доли машины с урезанным cpu_cores_effective пропускаются).
- **Что делает**: каждый цикл — таблица (id, $/ч, эфф./физ. ядра, RAM, диск, локация, CPU), события ПОЯВИЛАСЬ/УШЛА/ЦЕНА/ПАРАМЕТРЫ в `VastAI_logs/vast_track_events.log`, история снимков `scratch/vast_track_history.jsonl`, отчёт `results/vast_cpu_track_latest.txt`.
- **Живой прогон**: 4 актуальные станции (Ryzen 9 9950X Китай; EPYC 9654×3: Аризона, Венгрия×2; 0.0114–0.0123 $/ч).
- **Баги генератора паттернов исправлены**: двойной «Core Core Ultra», лишний токен в семействах («EPYC 4 4004»→«EPYC 4», «Xeon W-34 3400»→«Xeon W-34»).
- **Правило проекта**: скрипт НЕ арендует сам.

### [E10] destroy instance — проверка результата
- destroy instance вернул `null` без подтверждения статуса.
- Проверка: `vastai show instances --raw` → `[]`. Инстанс 54458725 удалён полностью
  (оплата остановлена, диск уничтожен). После destroy всегда сверять через show instances.

---

## 2026-10-06 — server removed
- Инстанс 54458725 (RTX 4060, i9-14900KF/32 vCPU) уничтожен по запросу пользователя.
- Перед destroy: показан статус (running), данные проекта на сервере отсутствовали.
- Проверено после: `show instances` → `[]`, аренд не осталось. Оплата остановлена.

---

## Шпаргалка проверенных команд

```bash
# 1. Список инстансов (без прокси!)
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai show instances --raw

# 2. Строка SSH-подключения
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai ssh-url <ID> --raw

# 3. Выполнить команду на сервере (через прокси SOCKS5)
ssh -o ProxyCommand="connect -S 127.0.0.1:10808 %h %p" -i ~/.ssh/vast_agent \
    -p 50049 root@201.132.16.122 'команда'

# 4. Загрузка CPU (снимок /tmp/s1,/tmp/s2) и частоты ядер на сервере:
#    cat /proc/cpuinfo | grep -E "processor|cpu MHz"; uptime; lscpu

# 5. ПОИСК СЕРВЕРОВ с отбором по названию процессора (cpu_name НЕ поисковый ключ!)
#    шаг 1: сузить поисковыми полями (gpu_name/num_gpus/cpu_arch/cpu_cores_effective/cpu_ghz)
#    шаг 2: --raw + клиентская фильтрация по подстроке cpu_name
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai search offers \
  'gpu_name=RTX_4090 num_gpus=1 cpu_cores_effective>=16' --limit 30 --order dph --raw 2>/dev/null \
| PYTHONIOENCODING=utf-8 python -c "
import json,sys
offers=json.load(sys.stdin)
sel=[o for o in offers if 'EPYC' in o['cpu_name']]  # EPYC/Xeon/Ryzen/Core i...
for o in sorted(sel,key=lambda x:x['dph_total'])[:5]:
    print(o['id'], o['cpu_name'], o['cpu_cores_effective'], round(o['dph_total'],3), o['geolocation'])
"

# 6. Справка: что реально уходит в API (внимание: печатает api_key в URL!)
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai search offers 'gpu_name=RTX_4090' --limit 1 --explain
# -> в JSON запроса видны принятые поля; cpu_name среди них НЕТ

# 7. ПОИСК CPU-ONLY СТАНЦИЙ (без видеокарт) — ОБЯЗАТЕЛЬНО num_gpus=0 [E11]
#    CPU-only НЕ попадают в обычную выдачу search offers даже без GPU-фильтра
#    (проверено: дефолтный запрос, limit 100 — ни одного num_gpus==0).
#    Базовый синтаксис (самые дешёвые первыми):
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai search offers \
  'num_gpus=0' --limit 200 --order dph --raw
#    Замечания: у CPU-only cpu_ram=0 (RAM не специфицирован) [N1]; cpu_name бывает None [E12].

# 8. CPU-ONLY + отбор по конкретным процессорам (клиентский фильтр по cpu_name, [E9])
env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy vastai search offers \
  'num_gpus=0' --limit 400 --order dph --raw 2>/dev/null \
| PYTHONIOENCODING=utf-8 python -c "
import json,sys
d=json.load(sys.stdin); targets=('14900KF','9555','9554','9654')
for o in d:
    n=o.get('cpu_name') or ''
    if any(t in n for t in targets):
        print(o['id'], round(o['dph_total'],4), n, o['cpu_cores_effective'], o.get('disk_space'), o.get('geolocation'))
"

# 9. МОНИТОР появления целевых станций (scripts/vast_monitor.py, проверен 2026-10-06):
#    каждые N сек ищет num_gpus=0; при НОВОМ совпадении с cpu_name/ценой/ядрами — сигнал
#    (звук + запись в VastAI_logs/vast_monitor_alerts.log). Скрипт НЕ арендует сам!
.venv/Scripts/python.exe scripts/vast_monitor.py --once --max-price 0.05 --min-cores 16
.venv/Scripts/python.exe scripts/vast_monitor.py --interval 60 --max-price 0.05 --min-cores 16
#    флаги: --cpu PATTERN (повторяемый), --with-gpu (учитывать и машины с GPU), --limit N;
#    состояние запомненных офферов: scratch/vast_monitor_state.json (алерты не повторяются)

# 10. ТРЕКЕР CPU-ONLY СТАНЦИЙ по списку good_cpu.txt (scripts/vast_track_cpu.py, проверен 13:36)
#     условия: num_gpus=0, не арендована, диск >= 4 GiB (--min-disk N), эфф. ядра == физ. ядра
#     (доли машины пропускаются). События -> VastAI_logs/vast_track_events.log,
#     история снимков -> scratch/vast_track_history.jsonl, отчёт -> results/vast_cpu_track_latest.txt
.venv/Scripts/python.exe scripts/vast_track_cpu.py --print-patterns   # только список 41 паттерна
.venv/Scripts/python.exe scripts/vast_track_cpu.py --once             # один снимок + отчёт
.venv/Scripts/python.exe scripts/vast_track_cpu.py --interval 300     # непрерывный трекинг
#     good_cpu.txt — источник правды: модели добавляются/удаляются в файле, паттерны пересчитываются
```

## 2026-10-06 — исправление классификации CPU-only (W2)

Снимки 10:51–10:52 UTC: стандартный num_gpus=0 дал 87/87 disk, расширенный 222/222 disk. Оффер 47225763 (EPYC 9654, цена с 20 GiB $0.01296/h) найден также в search volumes по machine_id=146217. Это хранилище, не аренда CPU. Прежние E11/N1/N2/N3/N4 не подтверждают доступность вычислительных CPU-only станций. Фильтры обоих трекеров исправлены: resource_type disk/unknown отклоняется. Регрессия прошла. Полный аудит: reports/operations/W2_cpu_market_20261006.md. Исходная история сохранена.

### Исправленные скрипты и проверка перед передачей в новый чат

- `scripts/vast_track_cpu.py`: до сравнения CPU/ядер/диска требует
  `resource_type` cpu/compute; disk и неизвестный тип отклоняет.
- `scripts/vast_monitor.py`: диск/неизвестный тип не проходит matches,
  включая режим --with-gpu; положительная метка CPU не подменяет вид контракта.
- `scripts/vast_w2.py`: отдельно фильтрует тип ресурса для CPU-only и GPU fallback,
  использует JSON/UTF-8 без HTTP(S)/ALL_PROXY и актуальный ssh-url.
- `tests/unit/test_vast_offer_classification.py`: реальная форма дешёвого disk
  оффера не проходит оба фильтра; явно заданный cpu проходит. Повтор: 1 passed
  за 0.05 s. Ruff passed. Аренда/SSH/численные движки этим тестом не проверены.

Мониторинг: 29 снимков 10:50:10–11:10:45 UTC, подходящих CPU-only compute
до $0.02/h нет. История: `results/evidence/W2_cpu_monitor_20261006.json`.
Пользователь разрешил fallback с GPU <=$0.12/h, только CPU вычисления,
общий бюджет $3, до 3 часов, одна аренда; план W2_test_rental_v2.md.
Команда rent после окна поиска вернула «Нет оффера в согласованных границах»
**до create**. Instance ID не получен; текущий show instances вернул `[]`.
Нет новой аренды, оплачиваемых ресурсов или выполненного научного прогона.

Передача всей реализации и список файлов:
`docs/handoffs/W2_three_engines_session_20261006.md`.
Корневую памятку теперь сопровождают сохранённая прежняя версия
`references/archives/vastai_cli_cheatsheet_20260923.md` и отдельная регистрация
обновлённого SHA в source_manifest.json; структурный validate снова passed.
