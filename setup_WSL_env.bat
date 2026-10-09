@echo off
rem W2-T008: one-time setup of the Linux (WSL Ubuntu-24.04) environment for engines B/C.
rem System packages are installed as root via "wsl -u root" - no Linux password needed.
rem Log: scratch\claude\W2-T008_wsl_setup_*.log
chcp 65001 >nul
wsl -d Ubuntu-24.04 -u root -- bash -c "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3 python3-venv python3-dev build-essential"
if errorlevel 1 (
  echo ERROR: apt-get failed
  pause
  exit /b 1
)
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scripts/wsl/setup_wsl_env.sh
echo.
echo Done. Exit code: %ERRORLEVEL%
pause
