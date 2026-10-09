@echo off
rem W2-T008: pytest, ruff, short and full cache lookup benchmark.
rem Output is shown on screen and saved to scratch\claude\W2-T008_checks_*.log
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found in %CD%
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\run_w2_t008_checks.py
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
