@echo off
rem W2-P004 stage P2-1: default/tight compiled DOP853 and IAS15 on 48 cases at 1/10/100/1000 years, inside WSL.
rem Long run (several hours). Resumable: rerun this file to continue after an interruption.
rem Disable sleep in Windows power settings for the night. Log: scratch\claude\W2-T008_checks_p2_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py p2"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
