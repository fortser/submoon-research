@echo off
rem W2-T017: all active Vast.ai compute offers from 16 vCPU up to $1/h (free, read-only).
rem Console table + HTML page with sorting and filters; files in data\interim\vast_find\all\
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
.venv\Scripts\python.exe scripts\vast_find.py --profile all --open --top 40
pause
