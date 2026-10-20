#!/usr/bin/env python3
"""Диагностика 2 (W2-T017): группировка выдачи анонимного endpoint Vast.ai. Бесплатно, только чтение.

Гипотеза по probe.json и live2_*.json: endpoint «bundles» отдаёт ОДНО предложение на группу
(модель GPU, число GPU, базовая цена), независимо от CPU и машины, а фильтр цены работает по
dph_base (без хранилища). Проверки (~15 запросов, пауза 1 с):
  A. группа RTX 5090 x1 с базовой ценой 0.9333: сколько строк; затем повторы с исключением
     уже увиденных машин (machine_id notin [...]) — раскрываются ли скрытые члены группы;
  B. то же с исключением по id предложения (id notin [...]);
  C. узкое окно вокруг dph_base предложения 53317848 (0.5333) — находится ли оно;
  D. группа RTX 5090 x1, 0.9333 с vCPU>=48 — какой представитель при другом фильтре CPU.
Итог: results/evidence/W2_T017_checks_20261013/probe2.json
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://console.vast.ai/api/v0/bundles/'
OUT = ROOT/'results/evidence/W2_T017_checks_20261013/probe2.json'
FIELDS = ('id', 'machine_id', 'cpu_name', 'cpu_cores_effective', 'num_gpus', 'gpu_name', 'dph_base',
          'dph_total', 'geolocation')


def post(body, log, label):
    time.sleep(1.0)
    full = {'rentable': {'eq': True}, 'rented': {'eq': False}, 'type': 'on-demand', 'allocated_storage': 20,
            'limit': 1000, 'order': [['dph_total', 'asc']], 'cpu_arch': {'eq': 'amd64'}}
    full.update(body)
    request = urllib.request.Request(URL, data=json.dumps(full).encode(), method='POST',
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
    rows = [{k: o.get(k) for k in FIELDS} for o in rows if isinstance(o, dict)]
    entry = dict(label=label, body=body, status=status, count=len(rows), seconds=round(time.monotonic()-started, 2),
                 rows=rows)
    log.append(entry)
    print(f"{label:<28} {status:>8} {len(rows):>3}  " + '; '.join(
        f"{r['id']}/{r['machine_id']} {str(r['cpu_name'])[:22]} {r['cpu_cores_effective']}v {r['dph_base']:.4f}"
        for r in rows[:6]))
    return rows


def drain(base, key, log, label, rounds=6):
    seen_rows, excluded = [], []
    for index in range(rounds):
        body = dict(base)
        if excluded:
            body[key] = {'notin': excluded}
        rows = post(body, log, f'{label} #{index}')
        new = [r for r in rows if r[key] not in excluded]
        if not new:
            break
        seen_rows += new
        excluded += [r[key] for r in new]
    return seen_rows


def main():
    log = []
    group = {'num_gpus': {'eq': 1}, 'gpu_name': {'eq': 'RTX 5090'},
             'dph_total': {'gte': 0.9330, 'lte': 0.9337}, 'cpu_cores_effective': {'gte': 16}}
    a = drain(group, 'machine_id', log, 'A machine notin')
    b = drain(group, 'id', log, 'B id notin')
    c = post({'dph_total': {'gte': 0.5330, 'lte': 0.5337}, 'num_gpus': {'eq': 1}, 'gpu_name': {'eq': 'RTX 5070 Ti'}},
             log, 'C base-price window 0.5333')
    d = post(dict(group, cpu_cores_effective={'gte': 48}), log, 'D group, vCPU>=48')
    total_window = post({'dph_total': {'gte': 0.9330, 'lte': 0.9337}, 'cpu_cores_effective': {'gte': 16}},
                        log, 'E window 0.9333 any GPU')
    result = dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                  A_machine_notin=[r['id'] for r in a], B_id_notin=[r['id'] for r in b],
                  C_found_53317848=any(r['id'] == 53317848 for r in c),
                  D_ids=[r['id'] for r in d], E_count=len(total_window), log=log)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\nИтог:', json.dumps({k: v for k, v in result.items() if k != 'log'}, ensure_ascii=False))
    print('Сохранено:', OUT)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
