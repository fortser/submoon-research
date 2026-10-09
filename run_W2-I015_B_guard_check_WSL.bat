@echo off
rem W2-I015: check of the IAS15 (engine B) polynomial guard fix inside WSL, ~2-3 minutes.
rem Runs unit tests and scripts/diag_ias15_guard.py (synthetic large-t test plus the real failed case
rem himalia-inner-90-0.0 integrated by B to 1.5 years). Log: scratch\claude\W2-T008_checks_bguard_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py bguard"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
