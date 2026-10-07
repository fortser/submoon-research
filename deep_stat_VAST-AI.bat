rem Резервный режим мониторинга: поиск через аккаунт (CLI).
rem ВНИМАНИЕ: тратит суточную квоту поисковых строк аккаунта. Запускать редко.
.venv\Scripts\python.exe scripts\vast_track_cpu.py --transport cli --strategy bisect --once --max-price 1.0 --max-queries 12 --request-spacing 3
pause
