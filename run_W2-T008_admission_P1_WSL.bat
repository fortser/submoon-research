@echo off
rem W2-P004 stage P1: equivalence of the compiled DOP853 prototype and scipy engine A on 48 cases (20/200/2000 periods), inside WSL.
rem Log: scratch\claude\W2-T008_checks_p1_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py p1"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
