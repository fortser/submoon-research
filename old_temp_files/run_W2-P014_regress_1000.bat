@echo off
rem W2-P014 step 3 (W2-T044): himalia:uniform_log_a:0008 from t=0 to 1000 years with the drift-free B start.
rem The archived B job failed at ~808.9 years (W2-I034). One local WSL attempt, 1 CPU, limit 120 min.
rem Expected duration about 75 minutes. Do not let the PC sleep while it runs. No rental.
rem Log: scratch\claude\W2-P014_regress_20261020.log ; result: results\evidence\W2_B_regress_20261020\regress.json
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-P014_regress_20261020.log
echo === W2-P014 step 3 regression %DATE% %TIME% > "%LOG%"
echo === Windows: ruff >> "%LOG%"
.venv\Scripts\python.exe -m ruff check scripts\regress_b_cm_start_1000.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === WSL: regression run (progress every 500000 steps) >> "%LOG%"
echo Running... about 75 minutes. Progress is written to %LOG%
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1; timeout --signal=TERM --kill-after=30s 7500s ~/venvs/submoon-w2/bin/python scripts/regress_b_cm_start_1000.py >> scratch/claude/W2-P014_regress_20261020.log 2>&1; echo wsl exit $? >> scratch/claude/W2-P014_regress_20261020.log"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
