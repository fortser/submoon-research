@echo off
rem Read-only, three bounded searches. No account quota and NO rental.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\vast_find.py --profile p2_2a_station --cycles 3 --interval 30 --session-seconds 600 --max-seconds 120 --open
if errorlevel 1 echo Monitoring failed. No paid actions were performed.
pause
