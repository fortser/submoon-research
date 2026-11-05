@echo off
rem W2-T049: B version with checkpoint implementation id (W2-I039), docstring fix. Numerics must be unchanged.
rem Checks: ruff, tests (Windows and WSL), full WSL pytest, bitwise comparison with the previous B. Local, no rental.
rem About 5-8 minutes. Log: scratch\claude\W2-T049_checks_20261021.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-T049_checks_20261021.log
echo === W2-T049 checks %DATE% %TIME% > "%LOG%"
echo === Windows 1. ruff >> "%LOG%"
.venv\Scripts\python.exe -m ruff check src\submoon_research\dynamics\engine_compare.py tests\unit\test_ias15_cm_start.py scripts\check_b_numerics_unchanged.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 2. tests (REBOUNDx parts skip on Windows) >> "%LOG%"
.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider tests\unit\test_ias15_cm_start.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t049_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
