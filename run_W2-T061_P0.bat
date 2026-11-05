@echo off
rem W2-T061, step 3.3 (W2-P015): Phase 3 block P0 (Iapetus 0011, Himalia 0013, Iapetus 0006; 8 ladder steps to 1000 years).
rem WSL, 5 workers, about 15-30 min, deadline 150 min. To stop early: create file scratch\claude\W2-PH3.stop
rem Results: runs\W2-PH3-P0-<UTC>\. Log: scratch\claude\W2-T061_P0_20261026.log
chcp 65001 >nul
cd /d "%~dp0"
set LOG=scratch\claude\W2-T061_P0_20261026.log
echo === W2-T061 block P0 %DATE% %TIME% > "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t061_p0_wsl.sh
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
