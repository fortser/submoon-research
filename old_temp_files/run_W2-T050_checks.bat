@echo off
rem W2-T050: checks of the B144 runner (scripts/b144_regress.py) and transport scenario b144 before any rental.
rem Windows: ruff, transport tests, validate. WSL: server gate tests, transport tests, ruff, short devcheck.
rem Local only, no rental, no network. About 3-6 minutes. Log: scratch\claude\W2-T050_checks_20261021.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-T050_checks_20261021.log
echo === W2-T050 checks %DATE% %TIME% > "%LOG%"
echo === Windows 1. ruff on new and changed files >> "%LOG%"
.venv\Scripts\python.exe -m ruff check scripts\b144_regress.py scripts\vast_p2.py tests\unit\test_b144_regress.py tests\unit\test_vast_p2_b144.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 2. transport tests (vast_p2 runs from Windows Python) >> "%LOG%"
.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider tests\unit\test_vast_p2_b144.py tests\unit\test_vast_p2_2a.py tests\unit\test_vast_p2_continue.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 3. validate >> "%LOG%"
.venv\Scripts\python.exe scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t050_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
