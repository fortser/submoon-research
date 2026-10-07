rem Сводная статистика по мониторингу + CSV. Сортировка по возрастанию колонки:
rem polls, machines, min, avg, median, max, min_per_cpu, avg_per_cpu (префикс '-' — убывание).
.venv\Scripts\python.exe scripts\vast_market_report.py --latest --top 15 --sort min_per_cpu --csv data\interim\vast_market\report_latest.csv
pause
