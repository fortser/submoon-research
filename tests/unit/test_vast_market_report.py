"""Агрегация отчёта: частота моделей и min/avg/median цены."""
from pathlib import Path

from submoon_research.compute.cpu_targets import parse_targets
from submoon_research.compute.market_search import MarketConfig, candidate
from submoon_research.compute.market_store import MarketStore

import importlib.util

ROOT = Path(__file__).resolve().parents[2]


def load_report():
    spec = importlib.util.spec_from_file_location(
        'vast_market_report', ROOT/'scripts/vast_market_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def offer(**changes):
    return dict(id=1, machine_id=10, resource_type='gpu',
        cpu_name='AMD EPYC 9654 96-Core Processor', cpu_cores_effective=48., cpu_cores=192,
        cpu_ram=64000, num_gpus=1, gpu_name='RTX 4090', disk_space=200.,
        dph_total=.5, dph_base=.49, storage_total_cost=.01,
        rentable=True, rented=False, geolocation='PL') | changes


def collection():
    return dict(complete=True, incomplete=[], queries=[], offers=[], elapsed_seconds=.1)


def test_sort_models_ascending_none_last_and_descending():
    module = load_report()
    rows = [
        dict(model='b', polls_seen=1, min_usd_per_effective_cpu=0.2),
        dict(model='a', polls_seen=2, min_usd_per_effective_cpu=None),
        dict(model='c', polls_seen=1, min_usd_per_effective_cpu=0.1),
    ]
    module.sort_models(rows, 'min_per_cpu')
    assert [r['model'] for r in rows] == ['c', 'b', 'a']
    module.sort_models(rows, '-min_per_cpu')
    assert [r['model'] for r in rows] == ['b', 'c', 'a']
    module.sort_models(rows, 'polls')
    assert [r['model'] for r in rows] == ['b', 'c', 'a']


def test_report_frequency_and_prices(tmp_path):
    rules, cfg = parse_targets((ROOT/'good_cpu.txt').read_text(encoding='utf-8')), MarketConfig()
    store = MarketStore(tmp_path)
    store.set_targets(rules)
    epyc = candidate(offer(dph_total=.1), rules, cfg)
    ryzen = candidate(offer(id=2, machine_id=20, cpu_name='AMD Ryzen 9 9950X 16-Core Processor',
                            cpu_cores_effective=32., cpu_cores=32, dph_total=.5), rules, cfg)
    store.append('2026-10-07T00:00:00+00:00', collection(), [epyc, ryzen], {})
    epyc_again = candidate(offer(id=1, dph_total=.3), rules, cfg)
    store.append('2026-10-07T00:05:00+00:00', collection(), [epyc_again], {})
    store.close()

    data = load_report().report(tmp_path)
    assert data['complete_polls'] == 2 and data['seen_models'] == 2
    models = [row['model'] for row in data['models']]
    assert models == ['epyc 9654', 'ryzen 9 9950x']
    top = data['models'][0]
    assert top['polls_seen'] == 2
    assert data['data_polls'] == 2
    assert top['availability_fraction_of_polls_with_data'] == 1.0
    assert top['min_usd_h'] == 0.1 and top['avg_usd_h'] == 0.2
    assert top['median_usd_h'] == 0.2
    assert top['unique_machines'] == 1
    assert data['never_seen_targets']
