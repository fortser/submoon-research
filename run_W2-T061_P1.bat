@echo off
rem W2-T061, step 3.4 (W2-P015): Phase 3 block P1 (5 targets + 8 controls; 85 jobs to 1000 years).
rem WSL, 5 workers, forecast about 25-40 min, deadline 150 min. To stop early: create file scratch\claude\W2-PH3.stop
rem Results: runs\W2-PH3-P1-<UTC>\. Log: scratch\claude\W2-T061_P1_<date>.log
chcp 65001 >nul
cd /d "%~dp0"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash scratch/claude/w2_t061_block_wsl.sh P1
for /f "delims=" %%F in ('dir /b /o-d scratch\claude\W2-T061_P1_*.log') do (type "scratch\claude\%%F" & echo. & echo Done. Log: scratch\claude\%%F & goto :end)
:end
pause
