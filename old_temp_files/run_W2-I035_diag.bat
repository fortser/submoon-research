@echo off
rem W2-I035: why local acceptance of P2-2a did not reproduce 3168 fingerprints (canonical_volume).
rem Diagnostics only: nothing in runs/ or code is changed. About 3 minutes.
rem Log: scratch\claude\W2-I035_diag_20261016.log; reports: results\evidence\W2_I035_diag_20261016\
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-I035_diag_20261016.log
echo === W2-I035 diag %DATE% %TIME% > "%LOG%"
echo === Windows .venv (Python 3.10) >> "%LOG%"
.venv\Scripts\python.exe scripts\diag_units_repro_p2_2.py --run runs/W2-P2-2a-56220810 --reference scratch/claude/t019/ref_container_20261016.json --output results/evidence/W2_I035_diag_20261016/windows_venv.json >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/t019/wsl_diag_i035.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
