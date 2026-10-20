#!/usr/bin/env python3
"""Диагностика полноты анонимной выдачи Vast.ai (W2-T017, бесплатно, только чтение).

Сравнивает результаты vast_find p2_2a и all из run_W2-T017_live.bat (live2_*.json). Предложения p2_2a,
которых нет в all, проверяются отдельными запросами:
  a) по machine_id — существует ли предложение и его цена при диске 20 ГБ;
  b) узкое окно цены вокруг него с фильтрами all (vCPU>=16, диск>=20);
  c) то же окно с vCPU>=32;
  d) то же окно без фильтров vCPU/диска.
Плюс повтор одного ненасыщенного окна дважды — одинаковы ли ответы.
Около 20–30 запросов с паузой 1 с. Итог: results/evidence/W2_T017_checks_20261013/probe.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from submoon_research.compute.market_search import MarketConfig, SearchError, anonymous_request  # noqa: E402

FIND = ROOT/'data/interim/vast_find'
OUT = ROOT/'results/evidence/W2_T017_checks_20261013/probe.json'
BASE = 'rentable=true rented=false'


def ask(query, log, config):
    time.sleep(1.0)
    started = time.monotonic()
    try:
        rows = anonymous_request(query, config)
        status = 'ok'
    except SearchError as exc:
        rows, status = [], exc.kind
    entry = dict(query=query, status=status, count=len(rows), seconds=round(time.monotonic()-started, 2),
                 ids=sorted(o['id'] for o in rows if isinstance(o.get('id'), int)))
    log.append(entry)
    print(f"{status:>8} {len(rows):>3} {entry['seconds']:>6} с  {query}")
    return rows, entry


def main(limit=6):
    config = MarketConfig(storage_gb=20., timeout=60.)
    evidence = OUT.parent
    p2_path = evidence/'live2_p2_2a.json' if (evidence/'live2_p2_2a.json').is_file() else FIND/'p2_2a/latest.json'
    all_path = evidence/'live2_all.json' if (evidence/'live2_all.json').is_file() else FIND/'all/latest.json'
    p2 = json.loads(p2_path.read_text(encoding='utf-8'))
    al = json.loads(all_path.read_text(encoding='utf-8'))
    in_all = {r['offer_id'] for r in al['rows']}
    machines_all = {r['machine_id'] for r in al['rows']}
    missing = [r for r in p2['rows'] if r['offer_id'] not in in_all]
    missing.sort(key=lambda r: r['machine_id'] in machines_all)  # сначала машины, которых в all нет совсем
    log, cases = [], []
    print(f"p2_2a {p2['utc']}: {len(p2['rows'])}; all {al['utc']}: {len(al['rows'])}; нет в all: {len(missing)}")
    for row in missing[:limit]:
        case = dict(offer_id=row['offer_id'], machine_id=row['machine_id'], cpu=row['cpu_key'], vcpu=row['vcpu'],
                    price_60gb=row['usd_per_hour'], machine_in_all=row['machine_id'] in machines_all)
        found, _ = ask(f"machine_id={row['machine_id']} {BASE}", log, config)
        offer = next((o for o in found if o.get('id') == row['offer_id']), None)
        case['by_machine'] = dict(listed=offer is not None, offers_on_machine=[o.get('id') for o in found],
                                  price_20gb=offer.get('dph_total') if offer else None)
        if offer:
            low, high = offer['dph_total']-0.0005, offer['dph_total']+0.0005
            window = f'dph_total>={low:.17g} dph_total<={high:.17g}'
            for name, filters in (('all_filters', 'cpu_arch=amd64 cpu_cores_effective>=16 disk_space>=20'),
                                  ('vcpu32', 'cpu_arch=amd64 cpu_cores_effective>=32 disk_space>=20'),
                                  ('no_cpu_disk', '')):
                rows, entry = ask(' '.join(x for x in (BASE, filters, window) if x), log, config)
                case[name] = dict(listed=row['offer_id'] in entry['ids'], count=entry['count'])
        cases.append(case)
    leaves = [q for q in al['search']['receipts'] if q.get('status') == 'ok' and (q.get('count') or 0) < 64]
    repeat = None
    if leaves:
        leaf = max(leaves, key=lambda q: q['count'])
        first = ask(leaf['query'], log, config)[1]
        second = ask(leaf['query'], log, config)[1]
        repeat = dict(query=leaf['query'], original_count=leaf['count'], counts=[first['count'], second['count']],
                      same_ids=first['ids'] == second['ids'])
    result = dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), missing_total=len(missing),
                  cases=cases, repeat=repeat, log=log)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nИтог по случаям:')
    for c in cases:
        print(json.dumps({k: c.get(k) for k in ('offer_id', 'cpu', 'machine_in_all', 'by_machine', 'all_filters',
                                                 'vcpu32', 'no_cpu_disk')}, ensure_ascii=False))
    print('Повтор окна:', json.dumps(repeat, ensure_ascii=False))
    print('Сохранено:', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
