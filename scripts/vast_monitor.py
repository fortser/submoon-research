#!/usr/bin/env python3
"""Совместимый вход единого рынка CPU/compute по good_cpu.txt, без аренды."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.compute.cpu_targets import normalize_cpu  # noqa: E402
from submoon_research.compute.market_monitor import main  # noqa: E402

DEFAULT_MAX_PRICE = 1.0


def matches(offer, cpus, max_price=1., min_cores=4.):
    """Совместимость старой функции отбора; единый CLI ведёт полную историю."""
    if offer.get('resource_type') not in ('cpu', 'compute', 'gpu'):
        return False
    name = normalize_cpu(offer.get('cpu_name'))
    price = offer.get('dph_total')
    cores = offer.get('cpu_cores_effective')
    return (isinstance(price, (int, float)) and 0 <= price <= max_price
            and isinstance(cores, (int, float)) and cores >= min_cores
            and any(re.search(r'\b'+re.escape(normalize_cpu(p))+r'\b', name) for p in cpus))


if __name__ == '__main__':
    raise SystemExit(main())
