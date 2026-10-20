#!/usr/bin/env python3
"""Диагностика 3 (W2-T017): какие фильтры endpoint применяет ДО предела выдачи, какие — после.

Гипотеза (live3: досмотр с исключением машин не нашёл 8 машин, найденных узким профилем):
сервер берёт не больше ~64 кандидатов окна цены и лишь потом применяет часть фильтров,
поэтому ответ < 64 строк бывает неполным и исключение уже увиденных не помогает.
Метод: окно цены, где запрос только с ценой даёт полную страницу (64). Добавляем один
фильтр F. Если F применяется до предела — снова 64 (подходящих много); если после —
заметно меньше 64. Плюс плотность узких окон и поиск пропавшего 53317848.
Около 20 запросов с паузой 1 с, бесплатно, только чтение. Итог: probe3.json.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://console.vast.ai/api/v0/bundles/'
OUT = ROOT/'results/evidence/W2_T017_checks_20261013/probe3.json'


def post(extra, log, label):
    time.sleep(1.0)
    body = {'type': 'on-demand', 'allocated_storage': 20, 'limit': 1000, 'order': [['dph_total', 'asc']]}
    body.update(extra)
    request = urllib.request.Request(URL, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    started = time.monotonic()
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=60) as response:
            data = json.loads(response.read().decode('utf-8', 'replace'))
        rows = data.get('offers', []) if isinstance(data, dict) else []
        status = 'ok'
    except urllib.error.HTTPError as exc:
        rows, status = [], f'http_{exc.code}'
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        rows, status = [], type(exc).__name__
    ids = sorted(o['id'] for o in rows if isinstance(o, dict) and isinstance(o.get('id'), int))
    small = sum(1 for o in rows if (o.get('cpu_cores_effective') or 0) < 16)
    disks = sum(1 for o in rows if o.get('resource_type') not in ('gpu', 'cpu', 'compute'))
    entry = dict(label=label, body=extra, status=status, count=len(rows), seconds=round(time.monotonic()-started, 2),
                 ids=ids, below16=small, disk=disks)
    log.append(entry)
    print(f"{label:<34} {status:>6} {len(rows):>3}  (vCPU<16: {small}, диски: {disks})")
    return rows


def main():
    log = []
    base = {'rentable': {'eq': True}, 'rented': {'eq': False}}
    window = {'dph_total': {'gte': 0.80, 'lte': 1.0}}
    post(dict(base, **window), log, '1 только цена 0.80-1.00')
    for name, flt in (('cpu_cores_effective>=16', {'cpu_cores_effective': {'gte': 16}}),
                      ('num_gpus>=1', {'num_gpus': {'gte': 1}}),
                      ('disk_space>=20', {'disk_space': {'gte': 20}}),
                      ('cpu_arch=amd64', {'cpu_arch': {'eq': 'amd64'}}),
                      ('reliability>=0.95', {'reliability': {'gte': 0.95}}),
                      ('verified', {'verified': {'eq': True}})):
        post(dict(base, **window, **flt), log, '2 цена + '+name)
    post(dict(window), log, '3 цена без rentable/rented')
    for low in (0.80, 0.85, 0.90, 0.95):
        post(dict(base, dph_total={'gte': low, 'lte': round(low+0.05, 4)}), log, f'4 только цена {low:.2f}-{low+0.05:.2f}')
    rows = post(dict(base, dph_total={'gte': 0.52, 'lte': 0.54}), log, '5 только цена 0.52-0.54')
    found = any(o.get('id') == 53317848 for o in rows)
    rows16 = post(dict(base, dph_total={'gte': 0.52, 'lte': 0.54}, cpu_cores_effective={'gte': 16}), log,
                  '5 цена 0.52-0.54 + vCPU>=16')
    found16 = any(o.get('id') == 53317848 for o in rows16)
    result = dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), found_53317848=found,
                  found_53317848_with_vcpu16=found16, log=log)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n53317848 в окне 0.52-0.54:', found, '| с vCPU>=16:', found16)
    print('Сохранено:', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
