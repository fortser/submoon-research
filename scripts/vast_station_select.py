#!/usr/bin/env python3
"""Безопасный список кандидатов P2-2a: чтение снимка + бесплатная перепроверка.

Никогда не создаёт инстанс или approval.json. Вывод — предложение, не разрешение.
Старый снимок не служит основанием выбора; offline служит только воспроизведению.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.compute import finder  # noqa: E402


def select(document, *, request=None, offline=False, now=None, max_checks=5,
           budget_usd=12., max_hours=24., transfer_reserve_usd=2.4) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stamp = datetime.fromisoformat(document['utc'])
    if stamp.tzinfo is None:
        raise ValueError('Снимок должен содержать UTC/timezone')
    age = (now-stamp).total_seconds()
    if not offline and not -60 <= age <= 900:
        raise ValueError('Нужен свежий снимок (не старше 15 минут); повторите мониторинг')
    if not 1 <= max_checks <= 10:
        raise ValueError('max_checks: 1..10')
    if any(not math.isfinite(x) or x < 0 for x in (budget_usd, max_hours, transfer_reserve_usd)) or max_hours <= 0:
        raise ValueError('Некорректные пределы предложения')
    profile = finder.load_profile('p2_2a_station')
    catalog = finder.Catalog.load()
    rows = []
    for offer in document['offers']:
        row, reason = finder.evaluate(offer, profile, catalog)
        if reason is None and 'over_deadline' not in row['flags']:
            rows.append((row, offer))
    rows.sort(key=lambda pair: (pair[0]['verification'] != 'verified', finder.rank_key(pair[0], 'cost')))
    checks, candidates = [], []
    machines = set()
    for row, offer in rows:
        if len(checks) >= max_checks:
            break
        if row['machine_id'] in machines:
            continue
        machines.add(row['machine_id'])
        check = (dict(status='offline', reason='not_rechecked', offer=offer) if offline else
                 finder.recheck(offer, profile['requirements'], request=request))
        checks.append(dict(offer_id=row['offer_id'], machine_id=row['machine_id'],
                           status=check['status'], reason=check.get('reason')))
        if check['status'] == 'error':
            break  # не выполнять дополнительные запросы после сетевого/доступного отказа
        if check['status'] not in ('available', 'offline'):
            continue
        fresh = check['offer']
        if not isinstance(fresh, dict):
            checks[-1].update(status='error', reason='invalid_offer')
            break
        row, reason = finder.evaluate(fresh, profile, catalog)
        if reason or 'over_deadline' in row['flags']:
            checks[-1].update(status='requirements_changed', reason=reason or 'over_deadline')
            continue
        # Не отвергать быстрый дорогой/ч сервер из-за предположения, что любой арендуется на сутки.
        # Индивидуальный предел включает 1.5 ч на выгрузку; никакого продления сверх него.
        rental_hours = min(max_hours, math.ceil(row['estimate']['hours'][1]+1.5))
        if rental_hours < row['estimate']['hours'][1]+1.5:
            checks[-1].update(status='over_deadline', reason='insufficient_transfer_time')
            continue
        max_compute_storage = row['usd_per_hour']*rental_hours
        if max_compute_storage+transfer_reserve_usd > budget_usd+1e-9:
            checks[-1].update(status='over_budget', reason='hourly * max_hours + transfer reserve')
            continue
        candidates.append(dict(row=row, offer=fresh, projected_compute_storage_usd=row['estimate']['usd'],
                               max_compute_storage_usd=max_compute_storage, max_hours=rental_hours,
                               run_deadline_hours=rental_hours-1.5, transfer_reserve_usd=transfer_reserve_usd,
                               transfer_rates={k: fresh.get(k) for k in (
                                   'inet_down_cost', 'inet_up_cost', 'internet_down_cost_per_tb',
                                   'internet_up_cost_per_tb')},
                               risks=['hardware_and_parallel_speed_not_measured'] +
                                     ([] if row['verification'] == 'verified' else [row['verification']])))
    # Свежая перепроверка может изменить ранжирование относительно исходного снимка.
    candidates.sort(key=lambda c: (c['row']['verification'] != 'verified', finder.rank_key(c['row'], 'cost')))
    return dict(utc=now.isoformat(), source_utc=document['utc'], source_age_seconds=age,
                approved=False, rental_enabled=False, approval_required=True,
                status='offline_replay' if offline else 'candidate_found' if candidates else 'no_candidate',
                profile=profile, budget_usd=budget_usd, max_hours=max_hours,
                max_paid_instances=1, paid_retries=0, mode='on-demand', checks=checks,
                candidates=candidates, recommended_offer_id=candidates[0]['row']['offer_id'] if candidates else None,
                limitations=['Endpoint неполон; не замечено != занято.',
                             'Не разрешает rent; перед create требуется новое явное согласие и перепроверка.',
                             'GPU не ускоряет этот CPU-стенд; цена учитывает диск 60 ГБ.',
                             'Ставки трафика и объём пакета сверить перед согласованием бюджета.',
                             'Долгий запуск требует базовой проверки и проверенного исполнителя контроля расходов.'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offers', type=Path, default=ROOT/'data/interim/vast_find/p2_2a_station/offers.json')
    parser.add_argument('--output', type=Path, required=True, help='Новый JSON предложения; существующий не заменяется')
    parser.add_argument('--offline', action='store_true', help='Без сети, не подтверждает доступность')
    parser.add_argument('--max-checks', type=int, default=5)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error('Предложение уже существует: используйте новый путь')
    data = args.offers.read_bytes()
    result = select(json.loads(data), offline=args.offline, max_checks=args.max_checks)
    result['source_sha256'] = hashlib.sha256(data).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    print(f"{result['status']}: рекомендованный offer={result['recommended_offer_id']}; аренда отключена")
    for item in result['candidates']:
        r = item['row']
        print(f"{r['offer_id']} {r['cpu_key']} {r['vcpu']:g}/{r['host_vcpu']:g} vCPU "
              f"{r['verification']} ${r['usd_per_hour']:.3f}/ч, "
              f"{r['estimate']['hours'][0]:.1f}–{r['estimate']['hours'][1]:.1f} ч")
    return 0 if result['candidates'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
