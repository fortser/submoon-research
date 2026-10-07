#!/usr/bin/env python3
"""Отчёт по сессии мониторинга Vast.ai: частота моделей и цены.

Только чтение market.sqlite3; сетевых запросов и аренды нет.
Считает по полным опросам (complete=1), цена дедуплицируется до
(цикл, machine, allocation), как в MarketStore.statistics().

Примеры:
    .venv/Scripts/python.exe scripts/vast_market_report.py <папка сессии>
    .venv/Scripts/python.exe scripts/vast_market_report.py --latest
    .venv/Scripts/python.exe scripts/vast_market_report.py --latest --json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / 'data/interim/vast_market'


def connect(folder):
    db = Path(folder) / 'market.sqlite3'
    if not db.is_file():
        raise SystemExit('Нет market.sqlite3 в '+str(folder))
    con = sqlite3.connect('file:'+db.as_posix()+'?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def latest_session():
    if not SESSIONS.is_dir():
        raise SystemExit('Нет папки сессий '+str(SESSIONS))
    folders = [p for p in SESSIONS.iterdir() if (p/'market.sqlite3').is_file()]
    if not folders:
        raise SystemExit('Нет ни одной сессии с market.sqlite3')
    return max(folders, key=lambda p: (p/'market.sqlite3').stat().st_mtime)


def _round(value, digits=5):
    return None if value is None else round(value, digits)


def report(folder):
    con = connect(folder)
    try:
        total, complete = con.execute(
            'SELECT count(*), coalesce(sum(complete),0) FROM cycles').fetchone()
        statuses = {str(row[0]): row[1] for row in con.execute(
            'SELECT json_extract(payload,\'$.status\'), count(*) FROM requests GROUP BY 1')}
        rows = con.execute('''
            SELECT o.cpu_key, o.cycle_id, o.machine_key, o.configuration_key, o.price,
                   json_extract(o.configuration_key, '$[0]') AS effective
            FROM observations o JOIN cycles c ON c.id = o.cycle_id
            WHERE c.complete = 1
        ''').fetchall()
        # Дедупликация до (цикл, machine, allocation) — как знаменатель статистики.
        best = {}
        for row in rows:
            key = (row['cpu_key'], row['cycle_id'], row['machine_key'], row['configuration_key'])
            previous = best.get(key)
            if previous is None or row['price'] < previous['price']:
                best[key] = dict(row)
        models = {}
        for row in best.values():
            entry = models.setdefault(row['cpu_key'], dict(polls=set(), machines=set(), prices=[], per_cpu=[]))
            entry['polls'].add(row['cycle_id'])
            entry['machines'].add(row['machine_key'])
            entry['prices'].append(row['price'])
            effective = row['effective']
            if isinstance(effective, (int, float)) and effective > 0:
                entry['per_cpu'].append(row['price']/effective)
        never_seen = [row[0] for row in con.execute('''
            SELECT label FROM targets WHERE label NOT IN (
                SELECT DISTINCT t.target FROM target_sightings t
                JOIN cycles c ON c.id = t.cycle_id WHERE c.complete = 1)
            ORDER BY label''')]
    finally:
        con.close()
    result = []
    for cpu_key, entry in models.items():
        prices = entry['prices']
        per_cpu = entry['per_cpu']
        result.append(dict(
            model=cpu_key,
            polls_seen=len(entry['polls']),
            availability_fraction_of_complete_polls=_round(len(entry['polls'])/complete, 4) if complete else None,
            unique_machines=len(entry['machines']),
            samples=len(prices),
            min_usd_h=_round(min(prices)),
            avg_usd_h=_round(statistics.fmean(prices)),
            median_usd_h=_round(statistics.median(prices)),
            max_usd_h=_round(max(prices)),
            min_usd_per_effective_cpu=_round(min(per_cpu)) if per_cpu else None,
            avg_usd_per_effective_cpu=_round(statistics.fmean(per_cpu)) if per_cpu else None,
        ))
    result.sort(key=lambda r: (-r['polls_seen'], r['min_usd_h']))
    return dict(
        session=Path(folder).name,
        total_polls=total,
        complete_polls=complete,
        request_status_counts=statuses,
        seen_models=len(result),
        never_seen_targets=never_seen,
        models=result,
    )


def print_table(data, limit=None):
    models = data['models'] if limit is None else data['models'][:limit]
    print('Сессия: {} | полных опросов: {} из {} | моделей замечено: {}'.format(
        data['session'], data['complete_polls'], data['total_polls'], data['seen_models']))
    if data['request_status_counts']:
        print('Статусы запросов:', json.dumps(data['request_status_counts'], ensure_ascii=False))
    print()
    header = ('Модель', 'Опросов', 'Доступн.', 'Машин', 'Мин $/ч', 'Сред $/ч',
              'Медиана $/ч', 'Макс $/ч', 'Мин $/ядро', 'Сред $/ядро')
    print('{:<34}{:>8}{:>10}{:>7}{:>11}{:>11}{:>12}{:>11}{:>12}{:>12}'.format(*header))
    for row in models:
        print('{:<34}{:>8}{:>9.0%}{:>7}{:>11}{:>11}{:>12}{:>11}{:>12}{:>12}'.format(
            row['model'][:33], row['polls_seen'], row['availability_fraction_of_complete_polls'] or 0,
            row['unique_machines'], row['min_usd_h'], row['avg_usd_h'], row['median_usd_h'],
            row['max_usd_h'], row['min_usd_per_effective_cpu'] if row['min_usd_per_effective_cpu'] is not None else '-',
            row['avg_usd_per_effective_cpu'] if row['avg_usd_per_effective_cpu'] is not None else '-'))
    if data['never_seen_targets']:
        print('\nЦели из good_cpu.txt, ни разу не встреченные ({}):'.format(len(data['never_seen_targets'])))
        print(', '.join(data['never_seen_targets']))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('folder', nargs='?', type=Path, help='Папка сессии с market.sqlite3')
    parser.add_argument('--latest', action='store_true', help='Взять самую свежую сессию')
    parser.add_argument('--json', action='store_true', help='Вывести JSON')
    parser.add_argument('--top', type=int, help='Показать только N самых частых моделей')
    args = parser.parse_args(argv)
    if bool(args.folder) == bool(args.latest):
        parser.error('Укажите папку сессии или --latest')
    folder = args.folder if args.folder else latest_session()
    data = report(folder)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print_table(data, limit=args.top)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
