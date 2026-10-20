#!/usr/bin/env python3
"""Быстрый поиск станций Vast.ai под профиль задачи (W2-D023, W2-D025, W2-T017).

Бесплатно (анонимный endpoint, квота аккаунта не тратится), только чтение, без аренды.
Поиск до насыщения: несколько проходов разной формы с фильтрами наличия объединяются, пока
в пределах времени (обычно 1–3 мин); полнота рынка не оценивается.
--cycles N: ограниченный мониторинг с историей снимков, без фоновой службы и аренды.
Таблица в консоли, локальная HTML-страница с сортировкой и фильтрами, offers.json в формате
vast_p2, сквозная история машин. Профили — configs/compute/profiles/*.yaml.

Примеры:
    .venv/Scripts/python.exe scripts/vast_find.py --profile p2_2a --open
    .venv/Scripts/python.exe scripts/vast_find.py --profile all --open
    .venv/Scripts/python.exe scripts/vast_find.py --list-profiles
    .venv/Scripts/python.exe scripts/vast_find.py --profile p2_2a --replay <offers.json | снимок *.json.gz>
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import sys
import time
import uuid
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from submoon_research.compute import finder  # noqa: E402
from submoon_research.compute.finder_html import write_html  # noqa: E402

OUTPUT = ROOT/'data/interim/vast_find'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=1, allow_nan=False, default=str),
                         encoding='utf-8')
    temporary.replace(path)


def replay_offers(path):
    """Предложения из снимка монитора (snapshots/NNNNNN.json.gz), снимка рынка или offers.json."""
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        data = json.load(stream)
    if isinstance(data, dict) and 'collection' in data:
        return data['collection']['offers']
    if isinstance(data, dict) and 'offers' in data:
        return data['offers']
    raise SystemExit('Не распознан формат снимка: '+str(path))


def run(profile_name, *, replay=None, output=OUTPUT, top=25, open_page=False, request=None,
        with_history=True, quiet=False, max_seconds=finder.SWEEP_MAX_SECONDS, clock=None, sleep=None):
    profile = finder.load_profile(profile_name)
    offers = replay_offers(replay) if replay else None
    result = finder.find(profile, request=request, offers=offers, max_seconds=max_seconds, clock=clock, sleep=sleep)
    folder = Path(output)/profile['name']
    if with_history and replay is None:
        history = finder.History(Path(output)/'history.sqlite3')
        try:
            history.record(result['utc'], profile['name'], result['rows'])
            history.annotate(result['rows'])
        finally:
            history.close()
    save_json(folder/'offers.json', finder.offers_document(result))
    save_json(folder/'latest.json', {k: v for k, v in result.items() if k != 'raw'})
    page = write_html(folder/'latest.html', result)
    if not quiet:
        print(finder.console_table(result, top=top))
        print(f'\nHTML: {page}\noffers.json: {folder/"offers.json"}')
    if open_page:
        webbrowser.open(page.resolve().as_uri())
    return result, page


def monitor(profile_name, *, cycles=3, interval=30., session_seconds=600., output=OUTPUT,
            max_seconds=120., request=None, clock=None, sleep=None, quiet=False, open_page=False):
    """Конечная сессия только чтения; новые снимки не заменяют прежние доказательства.

    Удаление из очередного списка означает «не замечено», НЕ «машина занята».
    Каждый цикл использует свежие предложения, без подмешивания старых в выбор аренды.
    """
    if not 1 <= cycles <= 20 or any(not math.isfinite(x) or x < 0 for x in (interval, session_seconds, max_seconds)):
        raise ValueError('cycles: 1..20; конечные неотрицательные пределы')
    if session_seconds <= 0 or max_seconds <= 0:
        raise ValueError('Время сессии и поиска должно быть положительным')
    clock, sleep = clock or time.monotonic, sleep or time.sleep
    session_id = finder.utc_now().replace(':', '').replace('+', '_')+'-'+uuid.uuid4().hex[:8]
    folder = Path(output)/'sessions'/session_id
    folder.mkdir(parents=True, exist_ok=False)
    started, previous, snapshots = clock(), set(), []
    result, page = None, None
    for cycle in range(cycles):
        remaining = session_seconds-(clock()-started)
        if remaining <= 0:
            break
        result, page = run(profile_name, output=output, max_seconds=min(max_seconds, remaining),
                           request=request, clock=clock, sleep=sleep, quiet=quiet)
        visible = {r['offer_id'] for r in result['rows']}
        stamp = dict(cycle=cycle+1, utc=result['utc'], observed=len(visible),
                     newly_observed=sorted(visible-previous), not_reobserved=sorted(previous-visible),
                     search=result['search'])
        snapshots.append(stamp)
        save_json(folder/f'{cycle+1:03d}_offers.json', finder.offers_document(result))
        save_json(folder/f'{cycle+1:03d}_result.json', {k: v for k, v in result.items() if k != 'raw'})
        save_json(folder/'session.json', dict(session_id=session_id, profile=profile_name, snapshots=snapshots,
                                            requested_cycles=cycles, elapsed_seconds=clock()-started,
                                            rental_enabled=False))
        previous = visible
        if result['search']['stop_reason'] == 'access_error':
            break
        if cycle+1 < cycles:
            sleep(min(interval, max(0., session_seconds-(clock()-started))))
    if result is None or page is None:
        raise RuntimeError('Сессия не выполнила ни одного поиска')
    if open_page:
        webbrowser.open(page.resolve().as_uri())
    if not quiet:
        print(f'Сессия мониторинга: {folder}; циклов {len(snapshots)}. Аренда отключена.')
    return result, folder


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', default='all', help='Имя профиля (configs/compute/profiles) или путь к YAML')
    parser.add_argument('--list-profiles', action='store_true', help='Показать профили и выйти')
    parser.add_argument('--open', action='store_true', help='Открыть HTML в браузере')
    parser.add_argument('--top', type=int, default=25, help='Строк в консоли (HTML — все)')
    parser.add_argument('--replay', type=Path, help='Без сети: offers.json или снимок *.json.gz')
    parser.add_argument('--output', type=Path, default=OUTPUT, help='Папка результатов')
    parser.add_argument('--max-seconds', type=float, default=finder.SWEEP_MAX_SECONDS,
                        help='Предел времени поиска до насыщения, с')
    parser.add_argument('--cycles', type=int, default=1, help='Конечное число опросов, 1..20; не фоновая служба')
    parser.add_argument('--interval', type=float, default=30., help='Пауза между завершёнными опросами, с')
    parser.add_argument('--session-seconds', type=float, default=600., help='Общий лимит мониторинга, с')
    args = parser.parse_args(argv)
    if args.list_profiles:
        for name in finder.list_profiles():
            profile = finder.load_profile(name)
            print(f"{name:<10} {profile['title']}")
        return 0
    if args.cycles != 1:
        if args.replay:
            parser.error('--cycles несовместим с --replay')
        result, _ = monitor(args.profile, cycles=args.cycles, interval=args.interval,
                            session_seconds=args.session_seconds, max_seconds=args.max_seconds,
                            output=args.output, open_page=args.open)
    else:
        result, _ = run(args.profile, replay=args.replay, output=args.output, top=args.top, open_page=args.open,
                        max_seconds=args.max_seconds)
    if result['search']['errors'] and not result['rows']:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
