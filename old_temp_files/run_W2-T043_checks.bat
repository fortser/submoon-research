@echo off
rem W2-T043 (W2-P014 step 2): B starts without centre-of-mass drift (engine_compare.ias15_start_state).
rem Checks: new tests, full WSL pytest, ruff, and a short smoke on 6 archived P2-2a B jobs at 100 years.
rem Local only, no rental. About 8-15 minutes. Log: scratch\claude\W2-T043_checks_20261020.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-T043_checks_20261020.log
echo === W2-T043 checks %DATE% %TIME% > "%LOG%"
echo === Windows 1. ruff on changed files >> "%LOG%"
.venv\Scripts\python.exe -m ruff check src\submoon_research\dynamics\engine_compare.py tests\unit\test_ias15_cm_start.py scripts\smoke_b_cm_start.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 2. new tests (REBOUNDx parts skip on Windows) >> "%LOG%"
.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider tests\unit\test_ias15_cm_start.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 3. validate >> "%LOG%"
.venv\Scripts\python.exe scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t043_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
