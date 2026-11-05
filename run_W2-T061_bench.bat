@echo off
rem W2-T061, step 3.2 (W2-P015): benchmark of the Phase 3 ladder (all P0/P1 jobs at 10 years) in WSL.
rem Not scientific; output in scratch only. 5 workers, hard deadline 30 min. Log: scratch\claude\W2-T061_bench_20261026.log
chcp 65001 >nul
cd /d "%~dp0"
set LOG=scratch\claude\W2-T061_bench_20261026.log
echo === W2-T061 benchmark %DATE% %TIME% > "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t061_bench_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
