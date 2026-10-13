@echo off
rem W2-T008 package 3 (stage D): shared event preparation - equivalence tests, ruff, before/after benchmark of engine A, inside WSL.
rem Log: scratch\claude\W2-T008_checks_events_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py events"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
