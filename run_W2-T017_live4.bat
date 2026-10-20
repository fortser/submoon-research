@echo off
rem W2-T017 re-check 4 after probe3 (price-only market scan): tests, ruff, validate, two independent LIVE scans,
rem cross-checks. About 4-7 minutes, free, read-only, no rental. Log: results\evidence\W2_T017_checks_20261013\live4.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\live4.log
if not exist "%OUT%" mkdir "%OUT%"
echo === W2-T017 live re-check 4 %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. LIVE scan + p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --profile p2_2a --top 15 --refresh >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\live4_p2_2a.json" >nul
echo === 5. all from the same snapshot >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 10 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\live4_all.json" >nul
echo === 6. offers of earlier narrow p2_2a (live3) missing now >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_p2_2a.json" --wide "%OUT%\live4_p2_2a.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 7. second independent LIVE scan, all >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 5 --refresh >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\live4b_all.json" >nul
echo === 8. scan 1 vs scan 2 >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live4_all.json" --wide "%OUT%\live4b_all.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live4b_all.json" --wide "%OUT%\live4_all.json" >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
