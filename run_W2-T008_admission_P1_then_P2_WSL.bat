@echo off
rem W2-P004 after audit W2-T009: rerun P1 (evaluator v2), then - only if P1 passes - the long P2-1 run, inside WSL.
rem P2-1 takes several hours and is resumable: rerun this file to continue. Disable Windows sleep for the night.
rem Logs: scratch\claude\W2-T008_checks_p1_*.log and scratch\claude\W2-T008_checks_p2_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py p1"
if errorlevel 1 (
  echo.
  echo P1 did not pass - P2-1 is NOT started. See the log.
  pause
  exit /b 1
)
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py p2"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
