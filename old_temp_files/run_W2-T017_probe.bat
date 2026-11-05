@echo off
rem W2-T017 probe: why some offers found by p2_2a are absent from the all search (free, read-only, ~1 min).
rem Log: results\evidence\W2_T017_checks_20261013\probe.log, data: probe.json
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=results\evidence\W2_T017_checks_20261013\probe.log
.venv\Scripts\python.exe scripts\vast_find_probe.py > "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
