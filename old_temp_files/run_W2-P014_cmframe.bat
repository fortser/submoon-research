@echo off
rem W2-P014 step 1 (W2-T042): fresh IAS15 restarts from snapshot C072 in the centre-of-mass frame.
rem G = CM frame (small coordinates, no drift); GF = same velocities, original huge coordinates.
rem Local WSL only, no rental. Tests first; then G and GF once each (each <=90 s, all <=600 s). About 1-2 minutes.
rem Log: scratch\claude\W2-P014_cmframe_20261020.log ; results: results\evidence\W2_B_cmframe_20261020\
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-P014_cmframe_20261020.log
echo === W2-P014 step 1 CM-frame controls %DATE% %TIME% > "%LOG%"
echo === Windows: ruff on new files >> "%LOG%"
.venv\Scripts\python.exe -m ruff check src\submoon_research\diagnostics\ias15_cm_frame.py scripts\diag_ias15_cm_frame.py scripts\run_ias15_cm_frame.py tests\unit\test_ias15_cm_frame.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === WSL: tests, prepare, variants, analysis >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_ias15_cm_frame.py >> scratch/claude/W2-P014_cmframe_20261020.log 2>&1; echo wsl exit $? >> scratch/claude/W2-P014_cmframe_20261020.log"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
