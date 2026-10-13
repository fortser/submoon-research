@echo off
rem W2-T014: checks of prototype v5 (high-e rule W2-D022), horizon independence, strict run verifier (W2-I020).
rem Full pytest, ruff, tracking validate, verify both P2-1 runs, short stand gate. About 10-20 minutes.
rem Log: scratch\claude\W2-T008_checks_d022_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py d022"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
