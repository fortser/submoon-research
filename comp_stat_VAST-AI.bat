rem Основной режим: анонимный публичный endpoint, квота аккаунта не тратится.
.venv\Scripts\python.exe scripts\vast_track_cpu.py --transport anonymous --strategy stratified --cycles 10 --interval 120 --max-price 1.0 --max-queries 64 --request-spacing 1
pause
