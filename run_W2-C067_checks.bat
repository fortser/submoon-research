@echo off
rem Historical entry point; runs only bounded OPT-01..05 checks, NOT the hour-long B diagnostic.
rem Unique UTC output directory and aggregate exit code are managed by the Python runner.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\check_w2_stabilization.py --suite short %*
set CHECK_EXIT=%ERRORLEVEL%
echo Check exit code: %CHECK_EXIT%
exit /b %CHECK_EXIT%
