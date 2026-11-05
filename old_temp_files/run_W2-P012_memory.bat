@echo off
rem W2-P012 (W2-T041): selective reset of IAS15 memory from snapshot C072.
rem Local WSL only, no rental. Tests first; then M0, MP, MC, MD, MA once each (each <=90 s, all <=600 s).
rem About 2-4 minutes. Log: scratch\claude\W2-P012_memory_20261020.log
rem Results: results\evidence\W2_B_memory_20261020\  (a second run is refused: directory already exists)
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set LOG=scratch\claude\W2-P012_memory_20261020.log
echo === W2-P012 memory controls %DATE% %TIME% > "%LOG%"
echo === Windows: ruff on new files >> "%LOG%"
.venv\Scripts\python.exe -m ruff check src\submoon_research\diagnostics\ias15_memory_controls.py scripts\diag_ias15_memory.py scripts\run_ias15_memory_controls.py tests\unit\test_ias15_memory_controls.py >> "%LOG%" 2>&1
echo exit %ERRORLEVEL% >> "%LOG%"
echo === WSL: tests, prepare, variants, analysis >> "%LOG%"
wsl -d Ubuntu-24.04 --cd "%~dp0." -- bash -c "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ~/venvs/submoon-w2/bin/python scripts/run_ias15_memory_controls.py >> scratch/claude/W2-P012_memory_20261020.log 2>&1; echo wsl exit $? >> scratch/claude/W2-P012_memory_20261020.log"
type "%LOG%"
echo.
echo Done. Log: %LOG%
pause
