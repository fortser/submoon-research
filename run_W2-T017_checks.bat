@echo off
rem W2-T017 checks: tests of the new finder and Vast scripts, ruff, validate,
rem offline replay of the 2026-10-13 monitor snapshot, then two LIVE free searches (read-only, no rental).
rem About 2-3 minutes. Log: results\evidence\W2_T017_checks_20261013\checks.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\checks.log
if not exist "%OUT%" mkdir "%OUT%"
echo === W2-T017 checks %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
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
echo === 5. replay p2_2a on monitor snapshot 2026-10-13 >> "%LOG%"
%PY% scripts\vast_find.py --profile p2_2a --top 12 --replay data\interim\vast_market\20261013T092814Z-ca994445\snapshots\000010.json.gz --output "%OUT%\replay" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 6. LIVE p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --profile p2_2a --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\live_p2_2a.json" >nul
echo === 7. LIVE all >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\live_all.json" >nul
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
