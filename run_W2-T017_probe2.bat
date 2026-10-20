@echo off
rem W2-T017 probe 2: does the anonymous Vast.ai endpoint group offers by GPU and price? (free, read-only, ~30 s)
rem Log: results\evidence\W2_T017_checks_20261013\probe2.log, data: probe2.json
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=results\evidence\W2_T017_checks_20261013\probe2.log
.venv\Scripts\python.exe scripts\vast_find_probe2.py > "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
