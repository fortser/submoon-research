@echo off
rem W2-T008 package 4 (stage F): compiled DOP853 prototype vs scipy engine A - tests, ruff, benchmarks, inside WSL.
rem Log: scratch\claude\W2-T008_checks_compiled_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py compiled"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
