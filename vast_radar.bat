@echo off
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" -X utf8 "scripts\vast_radar.py" --desktop
