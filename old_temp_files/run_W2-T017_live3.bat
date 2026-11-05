@echo off
rem W2-T017 re-check after W2-R018 (exclusion paging): vast tests, ruff, two LIVE free searches, cross-check.
rem About 2-4 minutes, read-only, no rental. Log: results\evidence\W2_T017_checks_20261013\live3.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\live3.log
if not exist "%OUT%" mkdir "%OUT%"
echo === W2-T017 live re-check 3 %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. LIVE p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --profile p2_2a --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\live3_p2_2a.json" >nul
echo === 4. LIVE all >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\live3_all.json" >nul
echo === 5. cross-check p2_2a vs all >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_p2_2a.json" --wide "%OUT%\live3_all.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
