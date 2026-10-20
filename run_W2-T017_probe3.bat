@echo off
rem W2-T017 probe 3: which Vast.ai endpoint filters apply before / after the page limit (free, read-only, ~40 s).
rem Log: results\evidence\W2_T017_checks_20261013\probe3.log, data: probe3.json
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=results\evidence\W2_T017_checks_20261013\probe3.log
.venv\Scripts\python.exe scripts\vast_find_probe3.py > "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
