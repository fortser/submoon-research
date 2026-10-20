@echo off
rem W2-D024: full Vast.ai market snapshot via YOUR ACCOUNT search quota (CLI, limit 1000), then P2-2a list.
rem Spends about 9-13 account search requests (limit 18 per 24 h, ledger data\interim\vast_find\account_requests.json).
rem Read-only, no rental. The snapshot is then reused by find_p2_2a.bat / find_all.bat for 60 minutes.
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\vast_find.py --scan-account --profile p2_2a --open
pause
