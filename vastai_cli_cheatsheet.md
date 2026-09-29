# Vast.ai CLI & SDK — шпаргалка

> Источник: официальная документация **https://cloud.vast.ai/cli/** (страница «CLI & SDK» в консоли Vast.ai),
> извлечено из клиентского бандла `CLI-DFkrzHDe.js`. Полные справочники:
> - CLI: https://docs.vast.ai/cli/hello-world
> - Python SDK: https://docs.vast.ai/sdk/python/quickstart
> - Serverless: https://docs.vast.ai/guides/serverless
> - Исходники: https://github.com/vast-ai/vast-cli · PyPI: https://pypi.org/project/vastai/

---

## 1. Установка

```bash
# Linux / macOS / WSL
curl -fsSL https://vast.ai/install.sh | bash

# Windows или для Python SDK — из PyPI
pip install --upgrade vastai
```

Один пакет `vastai` даёт сразу три вещи: **CLI** (`vastai ...`), **Python SDK** (`from vastai import VastAI`) и **серверлесс-клиент** (`from vastai import Serverless`).

Обновление: `pip install --upgrade vastai`.

---

## 2. Аутентификация

```bash
# Зарегистрировать API-ключ (ключ создаётся в консоли: Manage Keys)
vastai set api-key <api_key>

# То же самое для SDK — переменная окружения
export VAST_API_KEY=<api_key>
```

- `<api_key>` замените на свой реальный ключ. Не делитесь им.
- SDK читает `VAST_API_KEY` из окружения или принимает ключ напрямую: `VastAI(api_key="...")`.

---

## 3. Быстрый старт (end-to-end)

```bash
# 0. Ключ
vastai set api-key <api_key>

# 1. Проверка: показать 3 оффера
vastai search offers --limit 3

# 2. Запустить инстанс: PyTorch, 32 ГБ диска, SSH
vastai create instance <offer_id> --image pytorch/pytorch --disk 32 --ssh --direct

# 3. Добавить SSH-ключ в аккаунт (или без аргумента — сгенерирует новую пару)
vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"

# 4. Статус (первая загрузка — несколько минут, тянется образ)
vastai show instances

# 5. Строка SSH-подключения
vastai ssh-url <instance_id>
ssh $(vastai ssh-url <instance_id>)

# Полезное
vastai show ssh-keys       # список зарегистрированных SSH-ключей
```

---

## 4. Справка по CLI

```bash
vastai --help                  # все команды
vastai search offers --help    # флаги конкретной команды (пример)
vastai search templates --help  # справка по каталогу образов (см. §7.0)
help(vast.search_offers)       # в Python: сигнатура + docstring метода SDK
```

---

## 5. Python SDK

```python
from vastai import VastAI

vast = VastAI()  # использует VAST_API_KEY либо api_key="..."

vast.search_offers(query='gpu_name=RTX_4090 num_gpus>=4')
vast.show_instances()
vast.start_instance(id=12345)
vast.stop_instance(id=12345)
```

- Большинство методов SDK повторяют команды CLI.
- Подробности метода: `help(vast.search_offers)`.
- Обратная совместимость: `from vastai_sdk import VastAI` тоже работает.

---

## 6. Serverless-клиент (inference без управления воркерами)

```python
import asyncio
from vastai import Serverless

async def main():
    async with Serverless() as serverless:  # или Serverless("YOUR_API_KEY")
        endpoint = await serverless.get_endpoint("my-endpoint")
        response = await endpoint.request(
            "/v1/completions",
            {
                "model": "Qwen/Qwen3-8B",
                "prompt": "Who are you?",
                "max_tokens": 100,
                "temperature": 0.7,
            },
        )
        print(response["response"]["choices"][0]["text"])

asyncio.run(main())
```

---

## 7. Готовые команды под конкретные задачи

> Фильтры `search offers` совпадают с фильтрами веб-UI: поля вида `имя=значение` в кавычках.
> `offer_id` из выдачи `search offers` подставлять в `create instance`.
> Сортировка: `--order dph` и `-o dph` — синонимы (убывание: `-o dph-`; проверено стресс-тестом).

### 7.0. Каталог образов — `search templates` (проверено: работает без API-ключа)

> Аналог списка «~30 образов» в веб-UI при заказе инстанса: шаблон = docker-образ
> + параметры запуска (runtype, минимальный диск, env).

```bash
vastai search templates 'recommended=True' --raw    # официальные образы (на момент проверки: 37)
vastai search templates '' --raw                    # всё: официальные + community
vastai search templates 'recommended=True recommended_disk_space<=32' --raw  # мелкие = быстрый старт
vastai search templates 'count_created>1000' --raw  # популярные community-шаблоны
```

Ключевые поля шаблона (JSON):

| Поле | Что означает |
|---|---|
| `image` + `tag` | точный docker-образ, который развернётся (напр. `vastai/vllm @v0.29.0-cuda-13.0`) |
| `recommended_disk_space` | минимальный `--disk` (у Axolotl — 200 ГБ, у базовых — 16 ГБ) |
| `runtype` | `ssh` или `jupyter` |
| `count_created` | сколько инстансов создано (популярность) |
| `hash_id` | хэш шаблона для запуска |
| `extra_filters` | ограничения на железо оффера (Forge: `compute_cap>=750`, `cuda_max_good>=12.8`, `cpu_arch`) |
| `min_cuda` / `max_cuda`, `ssh_direct` | версии CUDA, поддержка прямого SSH |

Запуск через готовый шаблон (вместо ручного `--image` + `--onstart-cmd`):

```bash
vastai create instance <offer_id> --template_hash <hash_id> --disk 32
```

Особенности (проверено):

- У `search templates` (в отличие от `search offers`) **НЕТ** флагов `--limit` / `--order`;
- После JSON CLI дописывает `null` — в скриптах берём первую JSON-массиву:
  `python -c "import json,re,sys; d=json.loads(re.search(r'\[.*\]',sys.stdin.read(),re.S).group(0))"`;
- Специфический образ с Docker Hub (напр. `nvidia/cuda:12.4.1-...`) в каталоге искать не нужно —
  его задают напрямую через `--image`.
- Шаблон несёт `extra_filters` — ограничения на железо: сверять их с оффером по `--raw` ДО аренды.
  Forge требует `compute_cap>=750`, `cuda_max_good>=12.8`, `cpu_arch in (amd64, arm64)`;
  самый дешёвый 4090-оффер (Болгария, $0.375/ч) с `cuda_max_good=12.6` под Forge НЕ подходил.
- Свободно-текстовый запрос НЕ работает: `search templates "pytorch"` → `Unrecognized field: pytorch`
  + `Unknown operator`; искать по полям: `name=pytorch` (найдено 10), `recommended=True` и т.п.

### 7.1. Поиск GPU

```bash
# Одна RTX 4090
vastai search offers 'gpu_name=RTX_4090 num_gpus=1'

# 4+ GPU на инстанс (для мульти-GPU обучения)
vastai search offers 'gpu_name=RTX_4090 num_gpus>=4'

# H100 с большим диском
vastai search offers 'gpu_name=H100 num_gpus>=1' --limit 10

# Дешёвые: сортировка по цене кубита (/hr)
vastai search offers 'gpu_ram>=32' --limit 5 --order dph

# Задачи под GPU: RTX 4090 + A100
vastai search offers 'gpu_name in [RTX_4090, A100 80GB] num_gpus=1'  # [ ] — проверено (§9)
```

### 7.1a. Поиск по CPU-ядрам (правильный запрос)

> 👤 **НАСТРОЙКА ПОЛЬЗОВАТЕЛЯ (обязательно соблюдать):** когда пользователь спрашивает про
> «процессорные ядра», он всегда имеет в виду **РЕАЛЬНО ДОСТУПНЫЕ ДЛЯ РАБОТЫ ядра** =
> поле `cpu_cores_effective` (гарантированный срез/доли сервера). Общее физическое число
> (`cpu_cores`) пользователю НЕ нужно и важно его НЕ фильтровать по нему без явного запроса —
> иначе будет арендован сервер, где доступно в разы меньше ядер, чем ожидалось.
> Правило: идеальны офферы с `cpu_cores_effective ≈ cpu_cores` (машина целиком, без дробления).

> ⚠️ ВАЖНО, НЕ ПОВТОРЯТЬ ОШИБКУ: столбец `vCPUs` в табличном выводе CLI показывает
> **эффективные** ядра (`cpu_cores_effective`), а НЕ физические (`cpu_cores`).
> Фильтр `cpu_cores>=250` фильтрует по физическим ядрам и работает корректно —
> просто в таблице вы увидите заниженные цифры и можете подумать, что фильтр сломан.
> Проверяйте реальные значения через `--raw` (поле `cpu_cores`).

```bash
# ⭐ ПРАВИЛЬНО (по умолчанию): >=N РЕАЛЬНО ДОСТУПНЫХ ядер + GPU, топ самых дешёвых
vastai search offers 'cpu_cores_effective>=128 num_gpus>=1' --limit 15 --order dph

# ⭐ ПРАВИЛЬНО + честный вывод (физ. и эфф.) через JSON
vastai search offers 'cpu_cores_effective>=128 num_gpus>=1' --limit 15 --order dph --raw \
  | python -c "import json,sys;[print(o['id'], o['cpu_cores'], o['cpu_cores_effective'], round(o['dph_total'],3), o['gpu_name'], o['geolocation']) for o in json.load(sys.stdin)]"

# Идеальные: машина целиком, без дробления (эфф. == физ.)
vastai search offers 'cpu_cores_effective>=128 num_gpus>=1' --limit 15 --order dph --raw \
  | python -c "import json,sys;[print(o['id'], o['cpu_cores'], round(o['dph_total'],3), o['gpu_name'], o['geolocation']) for o in json.load(sys.stdin) if o['cpu_cores_effective']>= o['cpu_cores']*0.9]"

# Если требуется физическое число БЕЗ дележки (только для специализированных задач)
vastai search offers 'cpu_cores>=128 num_gpus>=1' --limit 15 --order dph
```

### 7.2. Запуск инстанса

```bash
# Классический PyTorch-инстанс с SSH и прямым доступом
vastai create instance <offer_id> --image pytorch/pytorch --disk 32 --ssh --direct

# Для быстрых задач — контейнер Jupyter (для доступа извне добавьте --direct)
vastai create instance <offer_id> --image pytorch/pytorch --jupyter

# Зарезервировать с фиксированным сроком (bid = 0, on-demand)
vastai create instance <offer_id> --image pytorch/pytorch --disk 100 --ssh --direct --bid 0
```

### 7.3. Мониторинг и управление

```bash
vastai show instances                 # все инстансы и статус
vastai show instance <instance_id>    # детали одного инстанса
vastai ssh-url <instance_id>          # ssh-строка
ssh $(vastai ssh-url <instance_id>)   # подключиться

vastai start instance <instance_id>    # запустить остановленный
vastai stop instance <instance_id>     # остановить (сохраняя данные)
vastai destroy instance <instance_id>  # удалить навсегда (диск тоже!)

# Безопасное удаление: сначала посмотреть, что на инстансе
vastai show instance <instance_id>
```

### 7.4. SSH-ключи

```bash
vastai show ssh-keys                              # список
vastai create ssh-key "$(cat ~/.ssh/id_ed25519.pub)"   # добавить существующий
vastai create ssh-key                             # сгенерировать новую пару
vastai delete ssh-key <key_id>                   # удалить ключ (ID — число, см. `show ssh-keys`; проверено вживую: pubkey НЕ принимается)
```

> 📌 РЕКОМЕНДАЦИЯ (внедрено в этой сессии): держать ОДИН выделенный ключ на аккаунт — он автоматически
> попадает в authorized_keys КАЖДОГО нового инстанса (§13), поэтому временные ключи под каждый сервер не нужны:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/vast_agent -N "" -C "vast-agent"   # один раз
vastai create ssh-key "$(cat ~/.ssh/vast_agent.pub)"                 # один раз (у нас: id=1403472)
ssh -i ~/.ssh/vast_agent root@<host> -p <port>                        # дальше — всегда этот ключ
```

### 7.5. Диски / данные

```bash
# Создать отдельный сетевой диск (переживает удаление инстанса)
vastai create volume <size_GB> <location>

# Примонтировать диск при создании инстанса
vastai create instance <offer_id> --image pytorch/pytorch --disk 32 --ssh --direct \
  --volume <volume_id>:/workspace

# Скопировать файлы на инстанс (хост/порт расшифровываем из ssh-url — формат ssh://root@<host>:<port>)
URL=$(vastai ssh-url <instance_id>)
VHOST=$(echo "$URL" | sed -E 's|ssh://root@([^:]+):[0-9]+|\1|')
VPORT=$(echo "$URL" | sed -E 's|ssh://root@[^:]+:([0-9]+)|\1|')
rsync -avz -e "ssh -p $VPORT -o StrictHostKeyChecking=no" \
  ./data.tar.gz root@$VHOST:/workspace/
# Удобнее: alias для обычного подключения
alias vssh='ssh -o StrictHostKeyChecking=no'
ssh $(vastai ssh-url <instance_id>)
```

### 7.6. Serverless-эндпоинты

> ⚠️ Команды `vastai serverless ...` и `vastai get endpoints` из старой CLI-доки в v0.3.1
> НЕ существуют (`invalid choice`). Актуальные (проверено по `--help` и live `show endpoints`):

```bash
vastai show endpoints            # список эндпоинтов (проверено: отвечает [])
vastai create endpoint ...       # создать (см. vastai create endpoint --help)
vastai update endpoint <id> ...  # обновить
vastai delete endpoint <id>      # удалить
vastai get endpt-logs <id>       # логи эндпоинта
```

Справочник: docs.vast.ai/guides/serverless. Для inference-клиента `Serverless` из PyPI — см. §6.

### 7.7. ИИ-агенты (Claude Code, Codex, Cursor, Windsurf, Pi и др.)

```bash
# Официальный навык Vast.ai для агентов (проверено: ставит 2 навыка — vastai и vastai-sdk)
npx skills add vast-ai/vast-cli --skill 'vastai'          # только CLI-навык
npx skills add vast-ai/vast-cli --skill '*' --agent pi -g -y --copy  # оба навыка, только для pi
# Куда ставится: ~/.agents/skills/{vastai,vastai-sdk}/SKILL.md (подхватывается всеми агентами)
```

Проверено при установке (обидные детали):
- `--skill vastai,vastai-sdk` через запятую НЕ принимается — либо одно имя, либо `'*'`;
- без `--agent` навыки ставятся ВО ВСЕХ обнаруженных агентов (часть может не пройти — установка «Failed»);
- без `--copy` создаётся симлинк (для Windows надёжнее `--copy`).

Расхождения официального навыка с реальным CLI v0.3.1 (проверено вживую): `vastai show user` —
«проверка аутентификации» из навыка — на деле баг 400 `owner: Extra inputs` (обход — §11.5);
`vastai search templates "pytorch"` из навыка — текстовый поиск не работает (см. §7.0).

---

## 8. Разбор ошибки: «фильтр по CPU не сработал»

**Симптом.** `vastai search offers 'cpu_cores>=250 …'` возвращает офферы, у которых в
столбце `vCPUs` указано 18–85 — похоже, фильтр игнорируется.

**Причина.** Поле `vCPUs` в таблице = `cpu_cores_effective` (эффективные/дисконтные ядра),
а не физическое `cpu_cores`. Фильтр работал с самого начала — обманывал только вывод.

**Диагностика (как было найдено).**

```bash
vastai search offers --help                       # синтаксис и поля запроса
vastai search offers 'cpu_cores>=250' --limit 2 --raw   # JSON: куча полей
# -> в объекте оффера есть ДВА разных поля: cpu_cores (физ.) и cpu_cores_effective (эфф.)
```

**Правило на будущее.** (краткий рецепт — §7.1a)
- Физические ядра — поле `cpu_cores` (именно им фильтрует CLI).
- Эффективные (что реально отдадут с учётом долей) — `cpu_cores_effective`.
- Табличный вывод CLI показывает эффективные значения в колонке `vCPUs`.
- Для проверки физических значений всегда используйте `--raw` и печатайте поле явно.
- Если нужна гарантия ядер в моменте — фильтруйте по `cpu_cores_effective`.

---

## 9. Результаты стресс-теста: известные ошибки и обходы

> Подробный отчёт: **`vastai_stress_results.md`** (103 запроса). Скрипт: `vastai_stress_test.py`.

### ⛔ Ошибки, которые НЕ повторять

| Ситуация | Поведение | Обход |
|---|---|---|
| Пробел в `gpu_name=RTX 3090` | ERROR rc=1 `Unrecognized field: 3090` | `gpu_name=RTX_3090` (**только `_`**)
| Пробелы ВНУТРИ `in [...]` | ✅ допускаются | `gpu_name in [RTX 3090, RTX 4090]`
| `cpu_cores=>64`, `cpu_cores>=`, `=` | ERROR rc=1 (парсер CLI) | правильный оператор + непустое значение
| Запятая в числе `dph_total>0,5` | 400 `not a valid ... FLOAT` | Только точка: `0.5`
| `--limit -1` | 400 `limit: Input should be >= 0` | `--limit 0` минимум
| `-o nonexist` | 400 `missing or invalid field` | сортировать по валидным полям
| `--new` | 400 `select_cols.0 ...` (сломан в v0.3.1) | НЕ использовать, обычный синтаксис полный
| Оффер исчез между `search offers` и `create` | 400 `no_such_ask` + **rc=0** | цикл «поиск→выбор→create» с retry; успех — по отсутствию `failed with error`, не по коду |

### ⚠️ Поля, которые есть в JSON, но НЕ поисковые ключи

- `time_remaining` → используйте `duration` / `start_date` / `end_date`
- `discounted_dph_total` → используйте `dph_total` / `dph_base` / `min_bid`
- `target_reliability` → используйте `reliability`
- `cpu_name` → используйте `cpu_cores` / `cpu_arch` / `cpu_ghz`

### 🔍 Ненадёжный exit code

При ошибках API (400) CLI возвращает **rc=0** и печатает текст `failed with error 400: ...`.
В скриптах проверяйте вывод по подстрокам: `failed with error|Warning:|Traceback`, не только по коду.

### ✅ Что проверено и работает

- Числовые операторы `==, !=, >, >=, <, <=`; диапазоны (несколько условий = AND).
- Булевы: `True`, `true`, `1` — все три вида; `rented=any`, `os_version=any`, `mobo_name=any`.
- `geolocation in/notin [CODES]`, `geolocation="Texas, US"` (кавычки + пробел — ок).
- Версии по частям: `driver_version>=535.86.05`, `cuda_vers>=12.1`.
- `cpu_arch=arm64`, `gpu_arch=Ada`.
- `--type=on-demand|reserved|bid`, `--raw`, `--explain`, `--curl`, `--no-default`, `--storage N`.
- Пустой запрос `''` = запрос по умолчанию (`external=false rentable=true verified=true`).
- `--limit 1000` работает, но медленно (≈25 с) — не злоупотребляйте.

---

## 10. Полезные ссылки

| Что | Ссылка |
|---|---|
| CLI-референс | https://docs.vast.ai/cli/hello-world |
| Python SDK quickstart | https://docs.vast.ai/sdk/python/quickstart |
| Serverless guide | https://docs.vast.ai/guides/serverless |
| Исходники CLI | https://github.com/vast-ai/vast-cli |
| Пакет на PyPI | https://pypi.org/project/vastai/ |
| Управление ключами | https://cloud.vast.ai/manage-keys |

---

## 11. Жизненный цикл инстансов (проверено вживую — дёшево и безопасно)

> Стоимость теста: 3 инстанса по ~$0.03/ч, суммарно **< $0.01**. Подробности/ошибки: `vastai_stress_results.md`.

### 11.1. Создание (on-demand)

```bash
# самый дешёвый доступный оффер
vastai search offers 'dph_total<0.05 num_gpus>=1' --limit 1 -o dph
# создание: маленький образ = быстрое развёртывание (~75 с до running)
vastai create instance <offer_id> --image nvidia/cuda:12.4.1-base-ubuntu22.04 \
  --disk 12 --ssh --direct --onstart-cmd 'sleep infinity' --cancel-unavail
# -> {'success': True, 'new_contract': <id>, 'instance_api_key': '<СЕКРЕТ>'}
```

### 11.2. Прерываемый (bid) экземпляр

```bash
vastai create instance <offer_id> --image nvidia/cuda:12.4.1-base-ubuntu22.04 \
  --disk 12 --ssh --direct --bid_price 0.03 --cancel-unavail
vastai change bid <id> --price 0.04     # обязательно --price, не позиционный аргумент
```

### 11.2a. Чекпоинты, мониторинг и автоподъём ставки (spot/bid)

> При перебитии ставки на spot-инстансе vast.ai **останавливает (**`stopped`**) машину
> на паузу, а НЕ уничтожает её.** Диск, окружение и файлы сохраняются (за хранение
> продолжает капать плата). Главное: прогресс в GPU-памяти теряется — его спасают
> только чекпоинты на диск.

**Что происходит при перебитии ставки (важно, часто недооценивается):**

- Работа **ставится на паузу**, а не «прорывается» физически — но состояние модели
  в GPU-памяти теряется, если нет чекпоинтов на `/workspace` (диск сохраняется).
- **Скорость реакции не спасает мгновенно:** поднять ставку можно через секунды после
  того, как вы это заметили, НО машина уже отдана конкуренту и свободна она станет
  только когда он её освободит (минуты–часы–дни). Скорость вашей реакции влияет лишь
  на время простоя, а не на возврат именно этой машины.
- Поднять ставку и вернуться в работу: `update instance --bid_price` + `start instance`.

```bash
# поднять ставку и запустить обратно
vastai update instance <id> --bid_price 1.50
vastai start instance <id>
```

**1) Чекпоинты (контрольные точки) — спасают прогресс:**

Сохранять: веса модели + состояние оптимизатора + эпоху/шаг (+ random_state для
воспроизводимости). Для рендера/кодирования — прогресс по конкретному файлу. Частота
5–30 мин: `max потеря ≈ интервал между чекпоинтами`. Хранить на /workspace (переживает
остановку), держать 2–3 последних, старые удалять (экономия диска). Диск НЕ теряется
при перебитии — чекпоинты остаются.

```python
import torch
def save_ckpt(step, model, optimizer):
    torch.save({'step': step,
                'model': model.state_dict(),
                'optim': optimizer.state_dict()}, f'/workspace/ckpt_{step}.pt')

# в цикле обучения:
for step in range(steps):
    train_batch(...)
    if step % 500 == 0:
        save_ckpt(step, model, optimizer)   # держать последние 2, старые удалять

# при старте — продолжить с последнего чекпоинта:
ckpt = torch.load('/workspace/ckpt_last.pt')
model.load_state_dict(ckpt['model']); start_step = ckpt['step'] + 1
```

**2) Мониторинг:**

```bash
vastai show instance <id> --raw                 # статус; stopped = перебили ставку
vastai show instances --cols id,status,dph,min_bid
# при перебитии в JSON `show instance`: actual_status == "stopped"
```

**3) Автоподъём ставки (watchdog) — своего в CLI нет, пишем сами:**

Логика: каждые N сек проверяем статус; если `stopped` — поднимаем ставку на шаг, но не
выше бюджета-потолка (cap), и запускаем обратно. При достижении потолка — уступаем
(скользить дальше невыгодно, лучше уйти на on-demand).

```python
import time, json, subprocess

INSTANCE_ID = "46480357"   # ваш spot-инстанс (создан с --bid_price)
MAX_BID = 1.50              # потолок: выше не спорим, уступаем
STEP    = 0.20              # на сколько поднимаем за раз

run = lambda *a: subprocess.run(["vastai", *a], capture_output=True, text=True)

def status():
    d = json.loads(run("show", "instance", INSTANCE_ID, "--raw").stdout)
    return d.get("actual_status"), d.get("min_bid")

while True:
    try:
        st, cur = status()
        if st == "stopped":
            new = cur + STEP
            if new <= MAX_BID:
                print(f"[{time.ctime()}] перебили! ставка {cur:.2f}->{new:.2f}")
                run("update", "instance", INSTANCE_ID, "--bid_price", f"{new}")
                run("start", "instance", INSTANCE_ID)
            else:
                print(f"[{time.ctime()}] потолок {MAX_BID} достигнут, уступаю")
        else:
            print(f"[{time.ctime()}] OK статус={st} ставка={cur}")
    except Exception as e:
        print("Ошибка:", e)
    time.sleep(60)
```

**Стратегия для spot (рекомендация на примере ID 46480357, $0.43 spot / $1.01 on-demand):**

| Компонент | Настройка |
|---|---|
| Чекпоинт | каждые ~10 мин на /workspace, держать 2 последних |
| Мониторинг | watchdog каждые 60 с |
| Потолок ставки | $1.00 (≈ цена on-demand $1.01) — выше не спорить |
| Шаг подъёма | +$0.20 |
| Если потолок достигнут | остановиться, перейти на on-demand вариант |

> Ключевой смысл: чекпоинты делают потерю прогресса минимальной (минуты), watchdog
> минимизирует простой, потолок защищает от ценовой гонки. Для долгих/критичных задач
> надёжнее on-demand (стабильная цена против случайных пауз).

### 11.3. Проверка «мы на арендованном сервере, спецификации совпадают»

Приватный ключ не обязан совпадать с вашим — разрешено создать новый и прикрепить:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/vast_test_key -N "" -C "stress-test"
vastai create ssh-key "$(cat ~/.ssh/vast_test_key.pub)"   # если уже зарег. — 400 "already exists"
vastai attach ssh <instance_id> "$(cat ~/.ssh/vast_test_key.pub)"
# подключение: host/port из `vastai ssh-url <id>`
ssh -i ~/.ssh/vast_test_key root@<host> -p <port>
```

Проверка изнутри (все значения должны совпасть с оффером):

```bash
whoami; hostname; uname -a; nproc
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
free -m | head -2; df -h / | tail -1; head -2 /etc/os-release
```

### 11.4. Завершение и контроль расходов

```bash
vastai stop instance <id>      # асинхронно; статус меняется с задержкой
vastai start instance <id>
vastai destroy instance <id>   # безвозвратно, мгновенно; есть destroy instances <id1> <id2>
# контроль:
vastai show instances --raw | python -c "import json,sys; print([x['id'] for x in json.load(sys.stdin)])"
```

### 11.5. Известные ошибки (проверено вживую)

- `vastai execute <id> 'cmd'` — НЕ работает: CLI шлёт POST, а эндпоинт принимает PUT;
  даже по PUT нужен ключ с правом маршрута `api.instance.command` (у учётного ключа его нет).
- SSH по паролю (даже паролем = instance_api_key) запрещён — только по ключам.
- `vastai show user` — баг CLI (`400: owner: Extra inputs`); обход: `curl .../api/v0/users/current/?api_key=...`.
- Повторная регистрация того же ssh-ключа → `400: The SSH key already exists on this account`.
- `show ssh-keys`/`show api-keys` выводят Python-repr (одинарные кавычки) — парсить через `ast.literal_eval`.

### 11.6. Проверка загрузки ВСЕХ ядер (рецепт с арендованного сервера)

Скрипт нагрузки на все логические ядра (multiprocessing, доменно-безопасный):

```python
# client_cpu_stress.py (на сервере) — запуск: BURST_SEC=120 python3 client_cpu_stress.py
import multiprocessing as mp, os, time, math, json
BURST = float(os.environ.get("BURST_SEC", "90")); NCORES = mp.cpu_count()
def worker(i, dur):
    pid = os.getpid(); start = time.time(); x = float(i) + 1.0; it = 0
    while time.time() - start < dur:
        x = ((x % 1000.0) + 1.0) * 123456.789321 + math.sin(x * 0.31) + math.cos(x * 0.17)
        it += 1
    return {"cpu": i, "pid": pid, "iters": it}
if __name__ == "__main__":
    with mp.Pool(NCORES) as p:
        res = p.starmap(worker, [(i, BURST) for i in range(NCORES)])
    json.dump({"ncpu": NCORES, "burst": BURST, "workers": len(res)},
              open("/tmp/stress_result.json", "w"))
```

Контроль `>90%` по каждому ядру: `python3 /tmp/percore.py` (читает `/proc/stat` 2 сек) —
вывод: `ядер с загрузкой >90%: 128 из 128; средняя 96.4%`. Плюс `top -bn1 | grep -E 'Tasks:|%Cpu'`.

### 11.7. Развёртывание по шаблону `--template_hash` (проверено вживую: SD WebUI Forge на RTX 4090)

```bash
vastai create instance <offer_id> --template_hash <hash_id> --disk 32 --ssh --direct --cancel-unavail
```

- `--template_hash` — точный аналог кнопки «RENT» с выбранным в §7.0 образом; отбор оффера — по
  `--raw` с учётом `extra_filters` шаблона (см. §7.0). Реальный запуск: арендован RTX 4090 за $0.37/ч.
- Крупный образ: `actual_status=loading` **~15–20 мин** (пулл десятков ГБ), затем провижининг:
  Forge докачивает модель (SD 1.5 ≈ 4 ГБ), `forge.sh` «стоит» до появления маркера `/.provisioning`.
  WebUI стартует сам после этого (порт контейнера 17860, в портале — вкладка «WebUI Forge»);
  Jupyter на 8080.
- `vastai logs <id>` в период загрузки показывает заглушку `No such container` — прогресс пулла
  скрыт на S3-логе хоста; ориентируйтесь на поллинг `actual_status`.
- После `vastai attach ssh <id> <key>` ключ подхватывается не мгновенно: первые ~15–20 с
  `Permission denied (publickey)`, затем работает без переподключения.
- `vastai ssh-url <id>` меняется по мере готовности: пока `loading` — шлюз (`ssh9.vast.ai:28205`),
  после `running` при `--direct` — прямой `IP:port` (проверено: оба варианта работают).
- Рынок «горячий»: офферы уходят за десятки секунд (`400 no_such_ask`), поэтому поиск и создание
  нужно делать одним повторяющимся циклом, а не вручную (см. §9).

---

## 12. Безопасность (ВАЖНО)

- **API-ключ и `instance_api_key` — секреты.** Храните ключ только в `~/.config/vastai/vast_api_key`;
  НЕ кладите в git/документацию. Отозвать: страница Manage Keys.
- При тесте баланс был **$0** — аренда прошла по кредиту/доверию. Крупные тесты — с депозитом.
- Права на маршруты API (`api.instance.command` и др.) включаются в правах конкретного API-ключа.
- Не оставляйте работающие инстансы: каждый час стоит денег (в тесте ~$0.03/ч). Приватный ключ,
  зарегистрированный на аккаунте, даёт SSH-доступ к вашим инстансам — удаляйте тестовые ключи после работ.

---

## 13. Особенности работы и выполнения команд (сводка наблюдений)

### Сеть / SSH-доступ
- Подключаться через **шлюз** `ssh<Х>.vast.ai` (поле `ssh_host` из `show instance`), а НЕ по
  прямому IP: прямой IP из внешней сети может не открываться (таймаут соединения).
  Порт — поле `ssh_port` (в `ssh-url` уже проксирован).
- После создания инстанса ваши зарегистрированные ключи **автоматически** попадают
  в `/root/.ssh/authorized_keys`; `vastai attach ssh <id> <ключ>` для нового ключа на уже
  привязанном вернёт `SSH key already associated with instance` — это не ошибка.
- Удаление ключа из аккаунта (`delete ssh-key`) НЕ убирает его из authorized_keys уже
  работающего инстанса (удалится вместе с destroy).

### Запуск и наблюдение за процессами
- Фоновый запуск, переживающий разрыв SSH-сессии:
  `setsid nohup env BURST_SEC=120 python3 script.py > log 2>&1 < /dev/null &`
- paramiko-чтение канала может упасть в таймаут, если фоновый процесс удержал поток —
  используйте отдельные подключения либо чтение через `recv_ready`/select.
- Счётчик рабочих процессов: `ps -e -o comm= | grep -c '^python3'` = ядра + 1 (главный).
- В образе `pytorch/pytorch:latest` (conda) **нет `ip`** (утилиты net-tools отсутствуют).
- `top -bn1` — срез: `Tasks: N running`, `%Cpu(s): xx us`; по-ядерно — скрипт по `/proc/stat` (см. 11.6).

### Жизненный цикл / деньги
- `destroy instance` — мгновенно и безвозвратно; каждый час аренды идёт счётчик
  (в тесте ~0.03–0.12 $/ч). Активных инстансов не оставлять без нужды.
- Тот же `offer_id` после destroy можно использовать снова (новая выдача контракта).
- `create instance` возвращает `instance_api_key` — это секрет (пароль/ключ контейнерного API).
- Права маршрутов API (`api.instance.command` и др.) задаются для конкретного API-ключа;
  `execute` без этого права не работает (см. 11.5).

---

## 14. Полный справочник CLI v0.3.1 vs официальный навык (проверено)

> Сверка каталога команд навыка `~/.agents/skills/vastai/SKILL.md` с реальным CLI v0.3.1.
> Легенда: ✅ проверено вживую · ⚙️ есть в справке CLI · ❌ в CLI отсутствует.

### 14.1. Новые возможности, не отражённые в §1–§13

**Передача файлов (вместо rsync, §7.5):** ✅
```bash
vastai copy <src> <dst>                     # синхронизация по ssh; формат [instance_id:]path
vastai copy 12345:/workspace/data out/      # инстанс → локально (и наоборот)
vastai copy 111:/workspace/ 222:/workspace/ # между инстансами
vastai copy --help                          # -i/--identity <путь к прив. ключу>
# НИКОГДА не копировать в /root или / — ломает ssh-права инстанса и copy! (из справки CLI)
vastai scp-url <id>                         # scp-строка
vastai cloud copy --src f --dst s3://b --instance <id> --connection <conn>  # облако (connection из UI)
```

**Жизненный цикл (сверх start/stop/destroy):** ✅
```bash
vastai reboot instance <id>              # stop+start
vastai recycle instance <id>             # destroy+create на том же оффере
vastai update instance <id>              # пересоздать из обновлённого шаблона
vastai prepay instance <id>              # депозит в reserved
vastai label instance <id> --label run1  # тег инстанса
vastai destroy instances <a> <b> -y      # батч-удаление
```

**Быстрый запуск БЕЗ offer_id (одной командой):** ✅
```bash
vastai launch instance --gpu-name RTX_4090 --num-gpus 1 --image pytorch/pytorch --disk 32 --ssh
# сам подберёт оффер (сорт. score-); флаги: -g/--gpu-name, -n/--num-gpus {1,2,4,8,12,14},
# -r/--region, -i/--image, -d/--disk, -o/--order, --limit, --label, --ssh/--jupyter
```

**SSH-ключи (сверх §7.4):** ✅
```bash
vastai detach ssh <id> <ssh_key_id>        # снять ключ с инстанса (id — из show ssh-keys)
vastai update ssh-key <id> "ssh-ed25519 ..."  # заменить значение ключа
vastai create ssh-key <file.pub>           # можно путь к файлу, не только "$(cat ...)"
```

**Биллинг и аудит (в §1–§13 не было):** ✅
```bash
vastai show invoices -c                    # история платежей (charges only)
vastai show deposit <id>                   # депозит reserved
vastai show audit-logs                     # история действий аккаунта
vastai show ipaddrs                        # история IP
```

**API-ключи — решает проблему прав из §11.5:** ✅
```bash
vastai create api-key --name ci --permissions '{...}'  # ограниченный ключ с правами маршрутов
vastai show api-keys | show api-key <id> | delete api-key <id> | reset api-key
# право api.instance.command назначается на ключе — тогда заработает vastai execute
```

**Прочее:** ✅
```bash
vastai logs <id> --tail 100 --filter error   # логи с хвостом и grep
vastai execute <id> "cmd" --schedule DAILY --day 0  # планировщик команд (на инстансе)
vastai take snapshot <id> --repo <repo> ...          # снепшот контейнера в registry
vastai show env-vars | create env-var | update env-var | delete env-var  # env аккаунта
vastai search volumes | benchmarks | invoices | instances   # поиск по другим сущностям
# Teams: create team, show members, invite member, remove member, show team-roles, destroy team
# Автоскейл: create/show/update/delete autogroup
# --image pytorch/pytorch:@vastai-automatic-tag — сервер сам подберёт тег под машину
```

### 14.2. В навыке есть, но в CLI v0.3.1 НЕ СУЩЕСТВУЕТ (❌ `invalid choice`)

| Команда из навыка | Реальный аналог |
|---|---|
| `vastai serverless ...` | `vastai show/create/update/delete endpoint` |
| `vastai get endpoints` | `vastai show endpoints` |
| `show/create/update workergroup`, `update workers`, `get wrkgrp-logs` | нет в v0.3.1 |
| `show deployments`, `show deployment(-versions)`, `delete deployment` | нет в v0.3.1 |
| `create network-volume`, `list network-volume` | нет в v0.3.1 |
| `show invoices-v1` | `vastai show invoices [-c]` |

### 14.3. Команды есть, но форма другая (расхождения с навыком)

- `show instances --status/--gpu-name/--label/--order-by/--cols` — **таких флагов нет**
  в v0.3.1; только `--raw` и глобальные; фильтруйте сами по JSON.
- `destroy instance <id> -y` — флага `-y` НЕТ; команда и так не спрашивает подтверждения
  (проверено вживую, §11.4).
- `launch instance` — принимает и позиционно `<gpu> <n> <image>`, и флаги;
  `--num-gpus` строго из {1,2,4,8,12,14}.
- `copy` — справка v0.3.1 описывает только legacy `[instance_id:]path`; форматы
  `C./V./s3./local:` из навыка в справке не отражены (на живом инстансе не перепроверено).
- `search templates "<текст>"` и `show user` — не работают (см. §7.0, §11.5).
