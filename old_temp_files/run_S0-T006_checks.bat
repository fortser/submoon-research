@echo off
rem S0-T006 (S0-D011): checks after removing the journal lock and new summaries.
rem Windows .venv: tracking tests, full pytest, ruff, validate, track render, no .write.lock left.
rem Several minutes. Log: scratch\claude\S0-T006_checks_20261013.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set LOG=scratch\claude\S0-T006_checks_20261013.log
echo === S0-T006 checks %DATE% %TIME% > "%LOG%"
echo === 1. pytest tests/unit/test_tracking.py >> "%LOG%"
%PY% -m pytest -q tests/unit/test_tracking.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. pytest full suite >> "%LOG%"
%PY% -m pytest -q >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 5. track render >> "%LOG%"
%PY% scripts\project.py track render >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 6. leftover lock >> "%LOG%"
if exist tracking\.write.lock (echo LOCK PRESENT >> "%LOG%") else (echo no lock >> "%LOG%")
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
