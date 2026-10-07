#!/usr/bin/env python3
"""Монитор всех compute-конфигураций по good_cpu.txt; предел поиска $1/h.

Заявленные CPU/квоты/цены записываются в SQLite, JSONL и сжатые снимки.
Дисковые контракты исключены. Аренды/платных действий нет.
Обычный запуск для будущего сбора: --duration-hours 72 --interval 300.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.compute.cpu_targets import parse_targets, source_lines, normalize_cpu  # noqa: E402
from submoon_research.compute.market_monitor import main  # noqa: E402


def read_good_cpu():
    return list(source_lines((ROOT/'good_cpu.txt').read_text(encoding='utf-8')))


def derive_patterns(lines):
    return [t.label for t in parse_targets('\n'.join(lines))]


def matches(offer, patterns, min_disk=4.):
    """Совместимость для прежних импортов; main использует общий строгий фильтр."""
    if offer.get('resource_type') not in ('cpu', 'compute', 'gpu') or offer.get('rented'):
        return False
    if (offer.get('disk_space') or 0) < min_disk:
        return False
    name = normalize_cpu(offer.get('cpu_name'))
    return any(re.search(r'\b'+re.escape(normalize_cpu(p))+r'\b', name) for p in patterns)


if __name__ == '__main__':
    raise SystemExit(main())
