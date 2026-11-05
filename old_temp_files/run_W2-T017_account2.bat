@echo off
rem W2-T017 account check 2: the account endpoint returns at most 512 rows per window (not 1000).
rem Seeds windows from the earlier free scan, then ONE account snapshot (at most 16 account search requests,
rem 2 already used today, limit 18 per 24 h). About 5-10 minutes, read-only, no rental.
rem Log: results\evidence\W2_T017_checks_20261013\account2.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\account2.log
echo === W2-T017 account check 2 %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. seed price windows from the free scan of 20:33 >> "%LOG%"
copy /y "%OUT%\live4_scan1_snapshot.json.gz" data\interim\vast_find\market_snapshot.json.gz >> "%LOG%"
echo === 4. ACCOUNT snapshot + p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --scan-account --profile p2_2a --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\market_snapshot.json.gz "%OUT%\account2_snapshot.json.gz" >nul
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\account2_p2_2a.json" >nul
echo === 5. all from the same snapshot >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 10 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\account2_all.json" >nul
echo === 6. free p2_2a 20:17 vs account >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_p2_2a.json" --wide "%OUT%\account2_p2_2a.json" >> "%LOG%" 2>&1
echo === 7. free all 20:17 vs account >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_all.json" --wide "%OUT%\account2_all.json" >> "%LOG%" 2>&1
echo === 8. account p2_2a inside account all >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\account2_p2_2a.json" --wide "%OUT%\account2_all.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
