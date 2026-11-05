@echo off
rem W2-T050 recheck: fixed unit test and coordinate sampling in scripts/b144_regress.py. Local, no rental. About 2 minutes.
rem Log: scratch\claude\W2-T050_recheck_20261021.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-T050_recheck_20261021.log
echo === W2-T050 recheck %DATE% %TIME% > "%LOG%"
.venv\Scripts\python.exe -m ruff check scripts\b144_regress.py tests\unit\test_b144_regress.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t050_recheck_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
