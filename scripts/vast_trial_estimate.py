#!/usr/bin/env python3
"""Простая офлайн-прикидка P2-2a по фактическим ресурсам проверочной аренды.

Вход JSON: cpu_name, physical_workers (полезные физические ядра, без SMT/E),
effective_vcpus (после affinity/cgroup), ram_gb (десятичные ГБ).
core_speed_ratio — измерение к P-ядру i9-14900K либо null для каталожной оценки.
Это не допуск к научной серии, не проверка железа и не разрешение продолжения.
"""
import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.compute import finder  # noqa: E402
from submoon_research.compute.radar import load_config, profile_for  # noqa: E402


def predict(hardware, hourly_usd, elapsed_hours=0, cfg=None):
    cfg = cfg or load_config()
    def number(value, name, zero=False):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or value < 0 or (not zero and value == 0)):
            raise ValueError('Неизвестный/неверный параметр '+name)
        return value
    hourly_usd = number(hourly_usd, 'hourly_usd')
    elapsed_hours = number(elapsed_hours, 'elapsed_hours', zero=True)
    useful = number(hardware.get('physical_workers'), 'physical_workers')
    effective = number(hardware.get('effective_vcpus'), 'effective_vcpus')
    ram = number(hardware.get('ram_gb'), 'ram_gb')
    entry = finder.Catalog.load().lookup(hardware.get('cpu_name'))
    measured_speed = hardware.get('core_speed_ratio')
    if measured_speed is None and entry is None:
        raise ValueError('CPU неизвестен: требуется короткий замер core_speed_ratio')
    entry = dict(entry or {})
    entry.pop('hybrid', None)  # physical_workers уже исключает E-ядра, SMT и недоступные ядра
    if measured_speed is not None:
        entry.update(speed=number(measured_speed, 'core_speed_ratio'), confidence='measured')
    workers = math.floor(min(useful, effective))
    row = dict(physical_cores=workers, ram_gb=ram, usd_per_hour=hourly_usd)
    estimate = finder.estimate(row, entry, profile_for(cfg)['workload'])
    if estimate is None:
        raise ValueError('Недостаточно ресурсов для оценки')
    total_hours = [elapsed_hours+h+cfg['wrapup_hours'] for h in estimate['hours']]
    total_usd = [h*hourly_usd+cfg['transfer_reserve_usd'] for h in total_hours]
    return dict(estimate=estimate, total_hours=total_hours, total_usd=total_usd,
                speed_confidence=entry.get('confidence'), speed_ratio=entry['speed'],
                within_budget=total_usd[1] <= cfg['full_run_budget_usd'],
                enough_workers=estimate['workers'] >= cfg['min_workers'],
                within_hourly_limit=hourly_usd <= cfg['max_hourly_usd'],
                continuation_approved=False, automatic_rental=False,
                limitations=['Приблизительная модель P2-2a, не измерение всей серии.',
                             'Setup 1 ч консервативно сохранён сверх уже прошедшего времени.',
                             'Трафик оценён резервом $1; фактические ставки/объём проверить.',
                             'Основная серия требует отдельного согласия; W2-D032 отменяет авто-destroy без ответа, но не расширяет бюджет.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hardware', type=Path, required=True)
    parser.add_argument('--hourly-usd', type=float, required=True, help='Включая контрактный диск')
    parser.add_argument('--elapsed-hours', type=float, default=0)
    args = parser.parse_args()
    result = predict(json.loads(args.hardware.read_text(encoding='utf-8')), args.hourly_usd, args.elapsed_hours)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
