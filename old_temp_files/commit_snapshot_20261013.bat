@echo off
rem P1 of the organization audit plan (2026-10-13): commit the accumulated state
rem BEFORE Claude changes the tracking code. Secrets in *_start.bat are gitignored.
rem Log: scratch\claude\commit_snapshot_20261013.log
chcp 65001 >nul
cd /d "%~dp0"
set LOG=scratch\claude\commit_snapshot_20261013.log
echo === git status before > "%LOG%"
git status --short >> "%LOG%" 2>&1
git add -A >> "%LOG%" 2>&1
git commit -F scratch\claude\commit_msg_snapshot_20261013.txt >> "%LOG%" 2>&1
echo commit exit code: %ERRORLEVEL% >> "%LOG%"
echo === after >> "%LOG%"
git log -1 --stat >> "%LOG%" 2>&1
git status --short >> "%LOG%" 2>&1
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
