@echo off
rem W2-T061 (W2-P015, steps 3.0-3.1): checks of the Phase 3 runner (scripts/phase3_convergence.py).
rem Windows: ruff, Phase 3 tests, full pytest, validate. WSL: tests incl. B, ruff, dry prepare of 3 blocks.
rem Local only, no rental, no scientific integrations. About 5-15 minutes. Log: scratch\claude\W2-T061_checks_20261026.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-T061_checks_20261026.log
echo === W2-T061 checks %DATE% %TIME% > "%LOG%"
echo === Windows 1. ruff on new files >> "%LOG%"
.venv\Scripts\python.exe -m ruff check scripts\phase3_convergence.py src\submoon_research\diagnostics\convergence.py tests\unit\test_phase3_convergence.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 2. Phase 3 tests >> "%LOG%"
.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider tests\unit\test_phase3_convergence.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 3. full pytest (W2-I037: two L1 CLI timeouts are a known flaky issue) >> "%LOG%"
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === Windows 4. validate >> "%LOG%"
.venv\Scripts\python.exe scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t061_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
