@echo off
rem W2-T008 package 2: full pytest, ruff, engine A vs fixed C (time breakdown) and multi-process scaling, inside WSL.
rem Log: scratch\claude\W2-T008_checks_ac_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py ac"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
