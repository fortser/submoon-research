@echo off
rem W2-T017: find Vast.ai offers for the P2-2a profile (free, read-only, no rental).
rem Console table + HTML page in the browser; files in data\interim\vast_find\p2_2a\
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\vast_find.py --profile p2_2a --open
pause
