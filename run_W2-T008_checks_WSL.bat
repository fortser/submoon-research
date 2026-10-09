@echo off
rem W2-T008: pytest, ruff and cache lookup benchmark inside WSL (Ubuntu-24.04).
rem Requires setup_WSL_env.bat to be run once. Log: scratch\claude\W2-T008_checks_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
