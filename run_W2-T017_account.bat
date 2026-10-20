@echo off
rem W2-T017 / W2-D024 check: tests, ruff, validate, ONE account snapshot (at most 10 account search requests),
rem lists from it, cross-checks with earlier free searches. About 3-6 minutes, read-only, no rental.
rem Log: results\evidence\W2_T017_checks_20261013\account.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\account.log
if not exist "%OUT%" mkdir "%OUT%"
echo === W2-T017 account check %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. vastai CLI >> "%LOG%"
where vastai >> "%LOG%" 2>&1
echo === 5. ACCOUNT snapshot + p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --scan-account --profile p2_2a --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\market_snapshot.json.gz "%OUT%\account_snapshot.json.gz" >nul
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\account_p2_2a.json" >nul
echo === 6. all from the same snapshot >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 10 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\account_all.json" >nul
echo === 7. earlier free p2_2a lists vs account snapshot >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_p2_2a.json" --wide "%OUT%\account_p2_2a.json" >> "%LOG%" 2>&1
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live4_p2_2a.json" --wide "%OUT%\account_p2_2a.json" >> "%LOG%" 2>&1
echo === 8. earlier free all vs account snapshot >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_all.json" --wide "%OUT%\account_all.json" >> "%LOG%" 2>&1
echo === 9. account p2_2a must be inside account all >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\account_p2_2a.json" --wide "%OUT%\account_all.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
