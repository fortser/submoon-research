@echo off
rem W2-T019: P2-2a rental readiness. Rule W2-D026, automatic baseline gate, vast_p2 scenario p2_2a,
rem search saturation on the relevant subset. No network, no rental. About 10 minutes.
rem Log: scratch\claude\W2-T019_checks_20261014.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set LOG=scratch\claude\W2-T019_checks_20261014.log
echo === W2-T019 checks %DATE% %TIME% > "%LOG%"
echo === 1. pytest full suite (Windows .venv, REBOUNDx tests skip here) >> "%LOG%"
%PY% -m pytest -q -rs >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. approval draft p2_2a vs candidate of 14.10 (no network) >> "%LOG%"
%PY% scratch\claude\t019\check_approval_profile.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 5. WSL part >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/t019/wsl_checks.sh
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
