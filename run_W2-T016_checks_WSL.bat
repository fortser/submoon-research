@echo off
rem W2-T016 (plan W2-P006): P2-2a stand, strict verifier, B last-step horizon clip (W2-I021).
rem Full pytest, ruff, validate, short stand gate + verifier, then cost measurement on the
rem probability sample (288 units, D/T 1 and 10 years, B 36 units 10 years) + verifier.
rem About 20-40 minutes. Log: scratch\claude\W2-T008_checks_p2_2_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_w2_t008_checks.py p2_2"
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
