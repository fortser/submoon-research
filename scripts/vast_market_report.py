#!/usr/bin/env python3
"""Отчёт по сессии мониторинга Vast.ai: частота моделей и цены.

Только чтение market.sqlite3; сетевых запросов и аренды нет.
Считает по циклам с наблюдениями, включая частичные (анонимный транспорт
штатно даёт complete=false из-за лимита 64 строк). Цена дедуплицируется до
(цикл, machine, allocation), как в MarketStore.statistics().

Примеры:
    .venv/Scripts/python.exe scripts/vast_market_report.py <папка сессии>
    .venv/Scripts/python.exe scripts/vast_market_report.py --latest
    .venv/Scripts/python.exe scripts/vast_market_report.py --latest --json
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics
from datetime import datetime, timezone
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


def _observation_count(folder):
    try:
        con = sqlite3.connect('file:'+(folder/'market.sqlite3').as_posix()+'?mode=ro', uri=True)
        try:
            return con.execute('SELECT count(*) FROM observations').fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        return 0


def latest_session():
    if not SESSIONS.is_dir():
        raise SystemExit('Нет папки сессий '+str(SESSIONS))
    folders = [p for p in SESSIONS.iterdir() if (p/'market.sqlite3').is_file()]
    if not folders:
        raise SystemExit('Нет ни одной сессии с market.sqlite3')
    # Оборванная сессия без наблюдений не должна перекрывать данные.
    with_data = [p for p in folders if _observation_count(p) > 0]
    pool = with_data or folders
    return max(pool, key=lambda p: (p/'market.sqlite3').stat().st_mtime)


def _round(value, digits=5):
    return None if value is None else round(value, digits)


CSV_COLUMNS = ['model', 'polls_seen', 'availability_fraction_of_polls_with_data',
               'unique_machines', 'samples', 'min_usd_h', 'avg_usd_h', 'median_usd_h',
               'max_usd_h', 'min_usd_per_effective_cpu', 'avg_usd_per_effective_cpu']


def write_csv(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(['generated_utc', 'session', 'data_polls', 'complete_polls'] + CSV_COLUMNS)
        for row in data['models']:
            writer.writerow([generated, data['session'], data['data_polls'], data['complete_polls']]
                            + [row.get(column) for column in CSV_COLUMNS])
    return path


def write_never_seen(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream, delimiter=';')
        writer.writerow(['session', 'target'])
        for target in data['never_seen_targets']:
            writer.writerow([data['session'], target])
    return path


# Колонки сортировки: имя в CLI -> поле модели. Префикс '-' даёт убывание.
SORT_FIELDS = {
    'polls': 'polls_seen',
    'machines': 'unique_machines',
    'min': 'min_usd_h',
    'avg': 'avg_usd_h',
    'median': 'median_usd_h',
    'max': 'max_usd_h',
    'min_per_cpu': 'min_usd_per_effective_cpu',
    'avg_per_cpu': 'avg_usd_per_effective_cpu',
}
SORT_CHOICES = ['model', 'polls', 'machines', 'min', 'avg', 'median', 'max',
                'min_per_cpu', 'avg_per_cpu']


def sort_models(models, spec):
    """Сортировка по возрастанию колонки; '-' впереди — по убыванию."""
    descending = spec.startswith('-')
    name = spec[1:] if descending else spec
    if name != 'model' and name not in SORT_FIELDS:
        raise SystemExit('Неизвестная колонка сортировки: {}. Доступно: {}'
                         .format(spec, ', '.join(['model'] + list(SORT_FIELDS))))
    def key(row):
        if name == 'model':
            return (0, row['model'])
        value = row[SORT_FIELDS[name]]
        if value is None:
            return (1, 0.0, row['model'])  # None всегда в конце
        return (0, -value if descending else value, row['model'])
    models.sort(key=key)
    return models


def report(folder, sort='min_per_cpu'):
    con = connect(folder)
    try:
        total, complete = con.execute(
            'SELECT count(*), coalesce(sum(complete),0) FROM cycles').fetchone()
        statuses = {str(row[0]): row[1] for row in con.execute(
            'SELECT json_extract(payload,\'$.status\'), count(*) FROM requests GROUP BY 1')}
        rows = con.execute('''
            SELECT o.cpu_key, o.cycle_id, o.machine_key, o.configuration_key, o.price,
                   json_extract(o.configuration_key, '$[0]') AS effective
            FROM observations o
        ''').fetchall()
        data_polls = len({row['cycle_id'] for row in rows})
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
                SELECT DISTINCT target FROM target_sightings)
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
            availability_fraction_of_polls_with_data=_round(len(entry['polls'])/data_polls, 4) if data_polls else None,
            unique_machines=len(entry['machines']),
            samples=len(prices),
            min_usd_h=_round(min(prices)),
            avg_usd_h=_round(statistics.fmean(prices)),
            median_usd_h=_round(statistics.median(prices)),
            max_usd_h=_round(max(prices)),
            min_usd_per_effective_cpu=_round(min(per_cpu)) if per_cpu else None,
            avg_usd_per_effective_cpu=_round(statistics.fmean(per_cpu)) if per_cpu else None,
        ))
    sort_models(result, sort)
    return dict(
        session=Path(folder).name,
        sort=sort,
        total_polls=total,
        complete_polls=complete,
        data_polls=data_polls,
        request_status_counts=statuses,
        seen_models=len(result),
        never_seen_targets=never_seen,
        models=result,
    )


def print_table(data, limit=None):
    models = data['models'] if limit is None else data['models'][:limit]
    print('Сессия: {} | циклов с данными: {} (полных {} из {}) | моделей замечено: {} | сортировка: {}'.format(
        data['session'], data['data_polls'], data['complete_polls'], data['total_polls'],
        data['seen_models'], data.get('sort', 'min_per_cpu')))
    if data['request_status_counts']:
        print('Статусы запросов:', json.dumps(data['request_status_counts'], ensure_ascii=False))
    print()
    header = ('Модель', 'Опросов', 'Доступн.', 'Машин', 'Мин $/ч', 'Сред $/ч',
              'Медиана $/ч', 'Макс $/ч', 'Мин $/ядро', 'Сред $/ядро')
    print('{:<34}{:>8}{:>10}{:>7}{:>11}{:>11}{:>12}{:>11}{:>12}{:>12}'.format(*header))
    for row in models:
        print('{:<34}{:>8}{:>9.0%}{:>7}{:>11}{:>11}{:>12}{:>11}{:>12}{:>12}'.format(
            row['model'][:33], row['polls_seen'], row['availability_fraction_of_polls_with_data'] or 0,
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
    parser.add_argument('--top', type=int, help='Показать только N моделей')
    parser.add_argument('--sort', default='min_per_cpu',
                        help='Колонка сортировки по возрастанию (префикс - для убывания): '
                             + ', '.join(SORT_CHOICES))
    parser.add_argument('--csv', type=Path,
                        help='Сохранить модели в CSV (UTF-8 BOM, разделитель ;); '
                             'рядом создаётся <имя>_never_seen.csv по ненайденным целям')
    args = parser.parse_args(argv)
    if bool(args.folder) == bool(args.latest):
        parser.error('Укажите папку сессии или --latest')
    folder = args.folder if args.folder else latest_session()
    data = report(folder, sort=args.sort)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    if args.csv:
        path = write_csv(args.csv, data)
        print('CSV: {} ({} строк)'.format(path, len(data['models'])))
        if data['never_seen_targets']:
            never = args.csv.with_name(args.csv.stem+'_never_seen.csv')
            write_never_seen(never, data)
            print('CSV (невстреченные цели): {} ({} строк)'.format(never, len(data['never_seen_targets'])))
    if not args.json and not args.csv:
        print_table(data, limit=args.top)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
