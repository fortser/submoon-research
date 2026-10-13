@echo off
chcp 65001 >nul
rem Скачивает Linux-пакеты для проверки стража W2-I015 в среде Claude (ничего не устанавливает).
cd /d "%~dp0"
set LOG=scratch\wheels\download.log
if not exist scratch\wheels mkdir scratch\wheels
echo === scipy (готовая сборка для Linux, Python 3.10) === > %LOG%
.venv\Scripts\python -m pip download --only-binary=:all: --platform manylinux2014_x86_64 --python-version 3.10 --implementation cp --no-deps -d scratch\wheels scipy==1.15.3 >> %LOG% 2>&1
echo === rebound 5.1.1 (исходники) === >> %LOG%
.venv\Scripts\python -m pip download --no-deps --no-binary=:all: -d scratch\wheels rebound==5.1.1 >> %LOG% 2>&1
echo === содержимое scratch\wheels === >> %LOG%
dir /b scratch\wheels >> %LOG%
type %LOG%
echo.
echo Готово. Напишите Claude: готово
pause
