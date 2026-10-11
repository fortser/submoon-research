@echo off
rem W2-I017: checks after the IAS15 guard fix (dt_last_done) and vast_p2 download() fix, inside WSL.
rem Full pytest, ruff, tracking validate, large-t guard diagnostics (ganymede-inner, t0 up to 10000 years)
rem and the short stand gate. About 10-20 minutes. Log: scratch\claude\W2-T008_checks_i017_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py i017"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
