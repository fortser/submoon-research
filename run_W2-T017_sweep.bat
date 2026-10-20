@echo off
rem W2-T017 / W2-D025 check: tests, ruff, validate, two LIVE free saturation searches (1-3 min each), cross-checks.
rem Read-only, no rental, no account quota. Log: results\evidence\W2_T017_checks_20261013\sweep.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PY=.venv\Scripts\python.exe
set OUT=results\evidence\W2_T017_checks_20261013
set LOG=%OUT%\sweep.log
echo === W2-T017 sweep check %DATE% %TIME% > "%LOG%"
echo === 1. pytest vast tests >> "%LOG%"
%PY% -m pytest -q -rs tests\unit\test_vast_finder.py tests\unit\test_vast_market_monitor.py tests\unit\test_vast_market_report.py tests\unit\test_vast_offer_classification.py tests\unit\test_vast_p2_download.py tests\unit\test_vast_p2_rerun.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 2. ruff >> "%LOG%"
%PY% -m ruff check src scripts tests >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 3. validate >> "%LOG%"
%PY% scripts\project.py validate >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === 4. LIVE p2_2a >> "%LOG%"
%PY% scripts\vast_find.py --profile p2_2a --top 15 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\p2_2a\latest.json "%OUT%\sweep_p2_2a.json" >nul
echo === 5. LIVE all >> "%LOG%"
%PY% scripts\vast_find.py --profile all --top 10 >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
copy /y data\interim\vast_find\all\latest.json "%OUT%\sweep_all.json" >nul
echo === 6. sweep p2_2a inside sweep all >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\sweep_p2_2a.json" --wide "%OUT%\sweep_all.json" >> "%LOG%" 2>&1
echo === 7. earlier free all 20:17 vs sweep all >> "%LOG%"
%PY% scripts\vast_find_crosscheck.py --narrow "%OUT%\live3_all.json" --wide "%OUT%\sweep_all.json" >> "%LOG%" 2>&1
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
