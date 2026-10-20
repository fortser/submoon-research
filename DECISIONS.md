# Решения проекта

Производный индекс; не редактировать вручную. Основания исходных решений: [bootstrap_decisions.md](docs/bootstrap_decisions.md). Одна запись целиком: `scripts/project.py track show --stage <этап> --id <ID>`.

| ID | Этап | Статус | Решение | Основание |
|---|---|---|---|---|
| S0-D001 | S0 | accepted | Сохранить оригинальные документы | [Запись](tracking/stages/S0/RECORDS.md#s0-d001) |
| S0-D002 | S0 | accepted | Сохранить среду и использовать pip | [Запись](tracking/stages/S0/RECORDS.md#s0-d002) |
| S0-D003 | S0 | accepted | Паспорта draft и кандидатный split | [Запись](tracking/stages/S0/RECORDS.md#s0-d003) |
| S0-D004 | S0 | accepted | Начальные локальные ресурсы | [Запись](tracking/stages/S0/RECORDS.md#s0-d004) |
| S0-D005 | S0 | accepted | Ограниченный проверочный L0 продукт | [Запись](tracking/stages/S0/RECORDS.md#s0-d005) |
| S0-D006 | S0 | accepted | Журнал событий и производные сводки | [Запись](tracking/stages/S0/RECORDS.md#s0-d006) |
| S0-D007 | S0 | rejected | Ручное дублирование статусов в нескольких файлах | [Запись](tracking/stages/S0/RECORDS.md#s0-d007) |
| S0-D008 | S0 | accepted | Предлагать Vast.ai при локальной серии свыше 5–6 часов | [Запись](tracking/stages/S0/RECORDS.md#s0-d008) |
| S0-D009 | S0 | accepted | Научный контекст: общая картина и полное досье выбранного этапа | [Запись](tracking/stages/S0/RECORDS.md#s0-d009) |
| S0-D010 | S0 | accepted | Последовательность определяется проверенными продуктами | [Запись](tracking/stages/S0/RECORDS.md#s0-d010) |
| S0-D011 | S0 | accepted | Оптимизация трекинга по аудиту 2026-10-13: минимальный набор без П3; блокировка журнала снята | [Запись](tracking/stages/S0/RECORDS.md#s0-d011) |
| W0-D001 | W0 | accepted | Локальный комплект статьи — первая точка аудита W0 | [Запись](tracking/stages/W0/RECORDS.md#w0-d001) |
| W0-D002 | W0 | accepted | Разовое исключение для загрузки исходных условий до 12 МиБ | [Запись](tracking/stages/W0/RECORDS.md#w0-d002) |
| W0-D003 | W0 | accepted | Разделить архивные входы и независимую постановку L1 | [Запись](tracking/stages/W0/RECORDS.md#w0-d003) |
| W0-D004 | W0 | accepted | Япет v1: GM SAT441, форма Thomas и разные уровни ошибок | [Запись](tracking/stages/W0/RECORDS.md#w0-d004) |
| W0-D005 | W0 | accepted | Гималия v1: GM JUP344 и раздельные ограничения размера | [Запись](tracking/stages/W0/RECORDS.md#w0-d005) |
| W0-D006 | W0 | accepted | Ганимед v1: GM JUP365, сводочный радиус и сохранение всех архивных стартов | [Запись](tracking/stages/W0/RECORDS.md#w0-d006) |
| W0-D007 | W0 | accepted | Геометрические состояния v1: новая эпоха TDB и общие оси ICRF | [Запись](tracking/stages/W0/RECORDS.md#w0-d007) |
| W0-D008 | W0 | accepted | Рабочая L1 v0.2: неизменный GM Гималии и явные пределы совместимости | [Запись](tracking/stages/W0/RECORDS.md#w0-d008) |
| W0-D009 | W0 | accepted | Аудит W0: сохранить проверенные входы, ограничить приёмку новых L1 | [Запись](tracking/stages/W0/RECORDS.md#w0-d009) |
| W0-D010 | W0 | accepted | Строгая приёмка L1 v0.3 и patch паспорта Ганимеда | [Запись](tracking/stages/W0/RECORDS.md#w0-d010) |
| W0-D011 | W0 | accepted | Принятие номинальной L1 и завершение W0 по проверенным продуктам | [Запись](tracking/stages/W0/RECORDS.md#w0-d011) |
| W1-D001 | W1 | accepted | Общий T=10000 лет, полный состав групп, пилот трёх хозяев и правила H1–H3 | [Запись](tracking/stages/W1/RECORDS.md#w1-d001) |
| W2-D001 | W2 | accepted | Три движка W2 и удалённый сравнительный пилот | [Запись](tracking/stages/W2/RECORDS.md#w2-d001) |
| W2-D002 | W2 | accepted | Одна тестовая аренда: CPU-only до $0.02/h либо CPU на GPU-сервере до $0.12/h | [Запись](tracking/stages/W2/RECORDS.md#w2-d002) |
| W2-D003 | W2 | accepted | Тестовая аренда точного оффера 54254159 для W2-T003 | [Запись](tracking/stages/W2/RECORDS.md#w2-d003) |
| W2-D004 | W2 | accepted | Аренда оффера 33051665 (Core i9-14900K) до 6 ч, не дороже $0.20/ч | [Запись](tracking/stages/W2/RECORDS.md#w2-d004) |
| W2-D005 | W2 | accepted | Подтверждать удалённый хост через verify; SSH через шлюз Vast | [Запись](tracking/stages/W2/RECORDS.md#w2-d005) |
| W2-D006 | W2 | accepted | Порог целостности dense IAS15 понижен 2e-13 -> 1e-9 с обоснованием | [Запись](tracking/stages/W2/RECORDS.md#w2-d006) |
| W2-D007 | W2 | accepted | Монитор рынка переведён на анонимный публичный endpoint | [Запись](tracking/stages/W2/RECORDS.md#w2-d007) |
| W2-D009 | W2 | accepted | Принято: аренда CPU-матрицы, бюджет $12, до 4 ч на машину | [Запись](tracking/stages/W2/RECORDS.md#w2-d009) |
| W2-D010 | W2 | accepted | Ориентир для оценки полного T=10000 лет — TR PRO 7995WX; ворота возврата к движку | [Запись](tracking/stages/W2/RECORDS.md#w2-d010) |
| W2-D012 | W2 | accepted | Не развивать C как движок отдельной орбиты; оптимизировать A (события, компилируемый цикл) | [Запись](tracking/stages/W2/RECORDS.md#w2-d012) |
| W2-D014 | W2 | accepted | Удалённый P2-1 на Vast.ai: вариант В, выполнение сегодня (вариант А), стенд v4 и срок 13.5 ч | [Запись](tracking/stages/W2/RECORDS.md#w2-d014) |
| W2-D015 | W2 | accepted | Тестовый прогон P2-1 на Threadripper PRO 5975WX (GPU-инстанс), пределы $4 и 5 ч | [Запись](tracking/stages/W2/RECORDS.md#w2-d015) |
| W2-D018 | W2 | accepted | Цензурирование прерванных заданий удалённого P2-1 | [Запись](tracking/stages/W2/RECORDS.md#w2-d018) |
| W2-D020 | W2 | superseded | Перезапуск P2-1 после W2-I017 — чистый полный прогон без засеивания D/T из 55200007 | [Запись](tracking/stages/W2/RECORDS.md#w2-d020) |
| W2-D016 | W2 | accepted | Замена вышедшего из строя 5975WX на i9-14900KF; 7995WX доступен только срезом 32 vCPU | [Запись](tracking/stages/W2/RECORDS.md#w2-d016) |
| W2-D017 | W2 | accepted | Продление тестового P2-1 на i9-14900KF до ~22 ч (разрешено до 24 ч) | [Запись](tracking/stages/W2/RECORDS.md#w2-d017) |
| W2-D013 | W2 | superseded | Высокие эксцентриситеты (e >= 0.9) до P2-1: только флаг вне проверенной области, правило — по данным | [Запись](tracking/stages/W2/RECORDS.md#w2-d013) |
| W2-D011 | W2 | accepted | Локальные прогоны движков B/C (IAS15+REBOUNDx) выполнять в WSL Ubuntu-24.04 | [Запись](tracking/stages/W2/RECORDS.md#w2-d011) |
| W2-D022 | W2 | accepted | Правило высоких e (заменит W2-D013): терминальная фаза ≤ 10 P0 в допуске, длительная — unresolved | [Запись](tracking/stages/W2/RECORDS.md#w2-d022) |
| W2-D021 | W2 | accepted | Перезапуск P2-1 после W2-I017: seed D/T 10000 лет из 55200007, остальное заново; i9 только P-ядра | [Запись](tracking/stages/W2/RECORDS.md#w2-d021) |
| W2-D019 | W2 | accepted | Автоматическое сворачивание отменено; finish вручную 2026-10-11 | [Запись](tracking/stages/W2/RECORDS.md#w2-d019) |
| W2-D023 | W2 | accepted | Поиск станций Vast.ai под профиль задачи: реализовать М1–М4 и HTML-страницу (W2-P008) | [Запись](tracking/stages/W2/RECORDS.md#w2-d023) |
| W2-D008 | W2 | accepted | Анонимный монитор: стратегия stratified (окна+jitter+смена K) | [Запись](tracking/stages/W2/RECORDS.md#w2-d008) |
| W2-D024 | W2 | accepted | Полный снимок рынка Vast.ai — через квоту аккаунта (CLI, лимит 1000), бесплатный быстрый режим — запасной | [Запись](tracking/stages/W2/RECORDS.md#w2-d024) |
| W2-D025 | W2 | accepted | Схему поиска выбирает Claude: бесплатный поиск до насыщения с оценкой охвата; снимок по цене (в т. ч. через аккаунт) убрать | [Запись](tracking/stages/W2/RECORDS.md#w2-d025) |
| W2-D026 | W2 | accepted | P2-2a: остановленные по сроку задания B на 1000 лет не блокируют решение по D–T | [Запись](tracking/stages/W2/RECORDS.md#w2-d026) |
| W2-D027 | W2 | accepted | Разрешение на одну аренду P2-2a: ≤ $0.40/ч, ≤ 22 ч, бюджет $12, выбор в пределах approval_p2_2a_draft | [Запись](tracking/stages/W2/RECORDS.md#w2-d027) |
| W2-D028 | W2 | accepted | Разрешена конкретная аренда EPYC 9654 offer 55276269 с исключениями RAM и reliability | [Запись](tracking/stages/W2/RECORDS.md#w2-d028) |
| W2-D029 | W2 | accepted | Все новые поиски Vast.ai: только verified или unverified, deverified исключить | [Запись](tracking/stages/W2/RECORDS.md#w2-d029) |
| W2-D031 | W2 | accepted | Радар 15 мин с автозапуском; одна условная проверочная аренда ≤30 мин/≤$1 | [Запись](tracking/stages/W2/RECORDS.md#w2-d031) |
| W2-D030 | W2 | superseded | Аренду и тесты отложить; сохранить 47034418 для периодического бесплатного мониторинга | [Запись](tracking/stages/W2/RECORDS.md#w2-d030) |
| W2-D032 | W2 | accepted | Отменён обязательный watchdog/авто-destroy без подтверждения; разрешена аренда и аппаратный тест 9975WX | [Запись](tracking/stages/W2/RECORDS.md#w2-d032) |
| W2-D033 | W2 | accepted | Разрешено продолжение P2-2a на iid56220810: 32 worker, до18ч отcreate и $13 всего | [Запись](tracking/stages/W2/RECORDS.md#w2-d033) |
| W2-D034 | W2 | accepted | Machine143224 (TR PRO9975WX) — первый кандидат для будущего бесплатного поиска | [Запись](tracking/stages/W2/RECORDS.md#w2-d034) |
| W2-D035 | W2 | accepted | Instance 56229721 — личная станция пользователя; агентам не изменять и не удалять | [Запись](tracking/stages/W2/RECORDS.md#w2-d035) |
| W2-D036 | W2 | accepted | Приёмка W2-P2-2a-56220810 архивным пакетом при ядре OpenBLAS сервера с допуском 1e-12 для производных float; генератор без BLAS | [Запись](tracking/stages/W2/RECORDS.md#w2-d036) |
| W2-D037 | W2 | accepted | Разрешена одна локальная диагностика B в WSL до 90 минут без повторов | [Запись](tracking/stages/W2/RECORDS.md#w2-d037) |
