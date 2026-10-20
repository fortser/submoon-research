# W2-T035: запуск одной локальной диагностики B

19.10.2026 · owner `pi-w2-b-capture-20261019` · разрешение `W2-D037` · preflight `W2-C071`.

**Статус этого отчёта: запуск проверен, результат ещё не получен.** Этот документ не является монитором; текущее исполнение проверять по процессам и heartbeat/final.

## Фактический запуск

- run_id: `W2-B-capture-20261019`.
- Локальная WSL `Ubuntu-24.04`, существующий Python `/home/taras/venvs/submoon-w2/bin/python` (3.12.3). REBOUND 5.1.1 / REBOUNDx 5.1.0 / NumPy 2.2.6; библиотеки не изменялись.
- Старт 11:04:53 UTC (14:04:53 МСК). Жёсткий предел завершения — примерно 12:34:53 UTC (15:34:53 МСК), одна попытка без повторов.
- GNU timeout PID 374 → supervisor PID 375 → worker PID 376. По фактическому `/proc/376/status`: 1 поток, CPU affinity 0, RSS 372096 KiB, VmSize 651252 KiB на 30-й секунде. RLIMIT_AS=2147483648 bytes; RLIMIT_FSIZE=251658240 bytes. Supervisor отдельно следит за суммарным RSS и суммарным размером выходов.
- Снимок перед первым шагом с `t >= 25525800000 s`, трасса от того же момента, ring512. Проход от исходного старта: старого пригодного снимка перед отказом нет.
- Процессы реально работают в фоне, независимо от завершения ответа в чате. Остановка: создать файл `results/evidence/W2_B_capture_20261019/attempt/STOP`. Это не останавливает другие WSL/пользовательские процессы. Не выключать компьютер/WSL, если требуется завершение этой попытки.

## Защита и проверки до интеграции

[Постановка/согласие](../../docs/tasks/W2_B_capture_20261019.md).

- SHA исходных summary/jobs, B implementation/job fingerprint и начальный probe совпали точно: [preflight](../../results/evidence/W2_B_capture_20261019/preflight.json). Preflight прошёл под тем же лимитом адресного пространства и CPU affinity, без интеграции.
- Windows полный pytest: **549 passed / 32 skipped**, 143.01 s; [лог](../../results/evidence/W2_B_capture_20261019/windows_tests.log), [JUnit](../../results/evidence/W2_B_capture_20261019/windows_tests.xml). Skips названы в логе: отсутствие REBOUNDx и Linux-only проверки.
- WSL целевые тесты: **25 passed / 0 skipped**, 9.20 s; [JUnit](../../results/evidence/W2_B_capture_20261019/wsl_tests.xml). Включены остановки wall/RSS/output/STOP, сохранение exit code, запрет повторного запуска и строгий профиль; прежние native replay/guard тесты также прошли.
- Ruff `src scripts tests`, validate, smoke прошли. LSP изменённых Python-файлов — clean.
- Изменены только диагностический бюджет/launcher, добавлены тесты. Общий CLI `diag_b_failure_p2_2.py` по-прежнему отказывает при >600 s; `configs/resources.yaml` не изменён. Силы, адаптер B, интеграторы, события и guards не менялись.
- До правок сохранены диагностические файлы и динамические модули с SHA: `scratch/w2_b_capture_20261019/baseline.json` и `baseline.zip`. Это локальная копия, не независимый backup.

## Файлы состояния и дальнейшая приёмка

Каталог: `results/evidence/W2_B_capture_20261019/attempt/`.

- `launch_manifest.json`: точная команда, диагностические аргументы, SHA кода и preflight, лимиты.
- `launcher_pid.json`, `supervisor_started.json`, `worker_pid.json`, `worker_started.json`: запуск и запрет повторов. Не удалять маркеры даже при ошибке.
- `heartbeat.json`: текущие время/RSS/объём и PID; `final.json` после остановки — только статус supervisor, не научный успех.
- `diagnostic.json` после штатной записи — фактический численный исход, trace, snapshot, same_failure. Код 1 ожидаем для partial или воспроизведённого guard и сам по себе не различает их.
- `diagnostic_artifacts/pre_step_snapshot/snapshot_manifest.json` при достижении порога — snapshot SHA/identity/environment. Отсутствие снимка по бюджету не разрешает повтор.
- `worker.log`, `supervisor.log`: ошибки/вывод.

Следующий исполнитель: проверить PID и свежесть heartbeat либо final; затем проверить SHA снимка и binary/physical файлов, исходную идентичность, точное время/ошибку и trace. Только после этого возможен короткий native replay. Не запускать `--launch` повторно и не править код работающего прохода. Историческая первопричина малого шага остаётся неизвестной; W2-I034 не закрыта, допуска W1/W3 нет.

Источники и ограничения из отчёта `reports/analysis/W2_OPT06_08_09_20261018.md` сохраняются. Аренды, расходов, исправления метода, high-e fallback и нового ансамбля нет.
