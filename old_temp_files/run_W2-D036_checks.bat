@echo off
rem W2-D036 / W2-I035: verifier --float-rtol, BLAS-free generator (exact_linalg), tests. No network. About 6 minutes.
rem Log: scratch\claude\W2-D036_checks_20261016.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set LOG=scratch\claude\W2-D036_checks_20261016.log
echo === W2-D036 checks %DATE% %TIME% > "%LOG%"
echo === 1. pytest full suite (Windows .venv) >> "%LOG%"
%PY% -m pytest -q -rs >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. WSL part >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/t019/wsl_checks_d036.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
