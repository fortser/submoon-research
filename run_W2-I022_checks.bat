@echo off
rem W2-I022: re-check in Windows .venv after skipping P2-2a stand tests without REBOUNDx and the ruff F541 fix.
rem Full pytest + ruff + validate, about 3 minutes. Log: scratch\claude\W2-I022_checks_20261013.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set LOG=scratch\claude\W2-I022_checks_20261013.log
echo === W2-I022 checks %DATE% %TIME% > "%LOG%"
echo === 1. pytest full suite >> "%LOG%"
%PY% -m pytest -q -rs >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
