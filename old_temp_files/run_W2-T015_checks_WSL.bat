@echo off
rem W2-T015: B (IAS15) step limit by local orbital time (W2-I019): full pytest, ruff, validate,
rem dense-output measurement (none vs local limit) and the short stand gate. About 10-20 minutes.
rem Log: scratch\claude\W2-T008_checks_b_step_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py b_step"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
