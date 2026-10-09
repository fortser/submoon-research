@echo off
rem W2-T008 package 5: multi-process scaling of the compiled DOP853 prototype vs scipy engine A, inside WSL.
rem Log: scratch\claude\W2-T008_checks_scaling_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py scaling"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
