"""W2-T017: поиск станций под профиль — каталог CPU, узкий запрос, оценка, HTML, vast_p2."""
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from submoon_research.compute import finder, market_search
from submoon_research.compute.cpu_targets import parse_targets
from submoon_research.compute.market_search import (
    MarketConfig, SearchError, initial_windows, query_to_body, recheck, scan_market,
)

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def catalog():
    return finder.Catalog.load()


def offer(i, cpu='AMD EPYC 9654 96-Core Processor', eff=192, host=192, price=0.339, **changes):
    value = dict(id=i, machine_id=1000+i, resource_type='gpu', num_gpus=1, gpu_name='RTX 5080',
                 cpu_name=cpu, cpu_arch='amd64', cpu_cores=host, cpu_cores_effective=eff, cpu_ram=64000,
                 disk_space=200, dph_total=price, dph_base=max(0., price-0.01), reliability=0.99,
                 verification='verified',
                 direct_port_count=10, rentable=True, rented=False, geolocation='Norway, NO')
    value.update(changes)
    return value


def market(offers, page=64, calls=None, lossy=False):
    """Поддельный анонимный endpoint по замерам 2026-10-13 (W2-R018, probe3).

    До предела страницы применяются только окно цены (по dph_base) и machine_id; из подходящих
    берутся первые 64 по id (не по цене); остальные фильтры (rentable, rented, vCPU…) —
    ПОСЛЕ предела, поэтому ответ с ними бывает меньше 64 и неполным."""
    def request(query, config):
        if calls is not None:
            calls.append(query)
        body = query_to_body(query, config)

        def passes(o, fields):
            for field, condition in body.items():
                if field not in fields:
                    continue
                value = o.get('dph_base' if field == 'dph_total' else field)
                for op, x in condition.items():
                    if op == 'eq' and value != x:
                        return False
                    if op == 'gte' and not (value is not None and value >= x):
                        return False
                    if op == 'lte' and not (value is not None and value <= x):
                        return False
                    if op == 'lt' and not (value is not None and value < x):
                        return False
                    if op == 'notin' and value in x:
                        return False
            return True
        pre = ('dph_total', 'machine_id')
        post = [f for f in body if f not in pre+('type', 'allocated_storage', 'limit', 'order')]
        if lossy:
            # Выдача зависит от формы запроса: каждая форма (набор полей) теряет свою треть.
            shape = '|'.join(sorted(k for k in body if k not in ('dph_total', 'type', 'allocated_storage',
                                                                   'limit', 'order')))
            rows = [o for o in offers if passes(o, pre+tuple(post))
                    and int(hashlib.sha256(f"{o['id']}|{shape}".encode()).hexdigest(), 16) % 3]
            return sorted(sorted(rows, key=lambda o: o['id'])[:page], key=lambda o: o['dph_total'])
        rows = sorted((o for o in offers if passes(o, pre)), key=lambda o: o['id'])[:page]
        rows = [o for o in rows if passes(o, post)]
        return sorted(rows, key=lambda o: o['dph_total'])
    return request


def profile(**requirements):
    data = dict(requirements=requirements)
    return finder.make_profile(data, 'test')


P2_2A = dict(workload=dict(core_hours=[280, 350], critical_path_hours=[12, 13], parallel_efficiency=0.78,
                           setup_hours=1.0, ram_gb_per_process=0.45, use_e_cores=False),
             requirements=dict(min_effective_cpus=32, max_hours=24, require_known_cpu=True))


@pytest.mark.parametrize('name, key, speed, source', [
    ('Intel(R) Core(TM) i9-14900K', 'Core i9-14900K', 1.00, 'measured'),
    ('13th Gen Intel(R) Core(TM) i9-14900KF', 'Core i9-14900KF/KS', 1.00, 'estimate'),
    ('AMD EPYC 9654 96-Core Processor', 'EPYC 9654', 0.64, 'measured'),
    ('AMD Ryzen Threadripper PRO 7995WX 96-Cores', 'Threadripper PRO 7995WX', 0.94, 'measured'),
    ('AMD Ryzen 9 9900X 12-Core Processor', 'Ryzen 9 9900X', 1.10, 'measured'),
    ('AMD EPYC 9755 128-Core Processor', 'EPYC 9755', 0.79, 'measured'),
    ('AMD Ryzen 9 9900X3D 12-Core Processor', 'Ryzen 9 9900X3D', 1.07, 'estimate'),
    ('AMD EPYC 9684X 96-Core Processor', 'EPYC 9684X', 0.62, 'estimate'),
    ('AMD EPYC 9554 64-Core Processor', 'EPYC 9004 (Zen 4)', 0.66, 'estimate'),
    ('AMD EPYC 75F3 32-Core Processor', 'EPYC 7xF3 (Zen 3, F)', 0.75, 'estimate'),
    ('AMD Ryzen 9 7900X3D 12-Core Processor', 'Ryzen 9 7950X3D/7900X3D', 0.95, 'estimate'),
    ('Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz', 'Xeon E5 v3/v4', 0.40, 'estimate'),
])
def test_catalog_lookup(name, key, speed, source):
    entry = catalog().lookup(name)
    assert entry['key'] == key and entry['speed'] == speed and entry['source'] == source


def test_catalog_unknown_cpu_and_every_target_has_speed():
    cat = catalog()
    assert cat.lookup('QEMU Virtual CPU version 2.5+') is None
    labels = [t.label for t in parse_targets((ROOT/'good_cpu.txt').read_text(encoding='utf-8'))]
    assert labels and [x for x in labels if cat.lookup(x) is None] == []


def test_measured_values_follow_cpu_matrix_report():
    report = (ROOT/'reports/operations/W2_cpu_matrix_20261008.md')
    if not report.is_file():
        pytest.skip('нет отчёта W2-T006')
    rates = {}
    for line in report.read_text(encoding='utf-8').splitlines():
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) > 2 and cells[1].replace('.', '').isdigit():
            rates[cells[0]] = float(cells[1])
    base = rates['Core i9-14900K']
    for entry in catalog().entries:
        if entry['source'] == 'measured':
            assert abs(entry['speed']-rates[entry['key']]/base) < 0.01, entry['key']


def test_describe_whole_slice_and_hybrid():
    cat = catalog()
    row, _ = finder.describe(offer(1), cat)
    assert row['physical_cores'] == 96 and row['share'] == 1.0
    row, _ = finder.describe(offer(2, cpu='Intel(R) Core(TM) i9-14900K', eff=16, host=32), cat)
    assert (row['p_cores'], row['e_cores'], row['share']) == (4, 8, 0.5)
    row, _ = finder.describe(offer(3, cpu='Intel Core Ultra 9 285K', eff=24, host=24), cat)
    assert (row['p_cores'], row['e_cores']) == (8, 16)


def test_estimate_epyc_9654_is_bound_by_longest_job():
    p = finder.make_profile(P2_2A, 'p')
    row, reason = finder.evaluate(offer(1), p, catalog())
    est = row['estimate']
    assert reason is None and est['bound'] == 'path'
    assert est['hours'][0] == pytest.approx(1+12/0.64) and est['hours'][1] == pytest.approx(1+13/0.64)
    assert est['usd'][1] == pytest.approx((1+13/0.64)*0.339)
    assert 'over_deadline' not in row['flags']


def test_estimate_small_slice_is_volume_bound_and_late():
    p = finder.make_profile(P2_2A, 'p')
    row, _ = finder.evaluate(offer(1, eff=32), p, catalog())
    est = row['estimate']
    assert est['bound'] == 'volume' and est['hours'][1] == pytest.approx(1+350/(0.64*16*0.78))
    assert 'over_deadline' in row['flags'] and 'slice' in row['flags']


def test_ram_limits_processes():
    p = finder.make_profile(P2_2A, 'p')
    row, _ = finder.evaluate(offer(1, cpu_ram=10000), p, catalog())
    assert row['estimate']['workers'] == 22 and 'ram_limits_workers' in row['flags']


def test_hybrid_uses_p_cores_unless_profile_allows_e_cores():
    data = json.loads(json.dumps(P2_2A))
    p = finder.make_profile(data, 'p')
    i9 = offer(1, cpu='Intel(R) Core(TM) i9-14900K', eff=32, host=32, price=0.15, cpu_ram=32000)
    row, _ = finder.evaluate(i9, p, catalog())
    assert row['estimate']['throughput_ref_cores'] == pytest.approx(8)
    data['workload']['use_e_cores'] = True
    row, _ = finder.evaluate(i9, finder.make_profile(data, 'p'), catalog())
    assert row['estimate']['throughput_ref_cores'] == pytest.approx(8+16*0.45)


@pytest.mark.parametrize('changes, requirements, reason', [
    (dict(resource_type='disk', num_gpus=0), {}, 'disk_contract'),
    (dict(rented=True), {}, 'unavailable'),
    (dict(dph_total=2.0), {}, 'price'),
    (dict(cpu_cores_effective=8), dict(min_effective_cpus=16), 'vcpu'),
    (dict(cpu_cores_effective=32), dict(min_share=0.9), 'share'),
    (dict(cpu_ram=16000), dict(min_ram_gb=32), 'ram'),
    (dict(disk_space=10), dict(disk_gb=60), 'disk'),
    (dict(reliability=0.9), dict(min_reliability=0.95), 'reliability'),
    (dict(verification='deverified'), dict(verification=['verified']), 'verification'),
    (dict(verification='deverified'), dict(verification=['verified', 'deverified']), 'verification'),
    (dict(verification='deverified'), {}, 'verification'),
    (dict(verification=None), {}, 'verification'),
    (dict(direct_port_count=0), dict(min_direct_ports=1), 'direct_ports'),
    (dict(cpu_name='Mystery CPU'), dict(require_known_cpu=True), 'unknown_cpu'),
    (dict(cpu_name='Intel Xeon E5-2680 v4'), dict(min_core_speed=0.6), 'slow_cpu'),
    ({}, dict(allow_gpu=False), 'gpu_not_allowed'),
    ({}, dict(cpu_regex='i9-1[34]900K'), 'cpu_regex'),
])
def test_rejections(changes, requirements, reason):
    _, got = finder.evaluate(offer(1, **changes), profile(**requirements), catalog())
    assert got == reason


@pytest.mark.parametrize('status', ['verified', 'unverified'])
def test_search_status_policy_accepts_only_current_allowed_statuses(status):
    row, reason = finder.evaluate(offer(1, verification=status), profile(), catalog())
    assert reason is None and row['verification'] == status
    for name in finder.list_profiles():
        allowed = finder.load_profile(name)['requirements']['verification']
        assert set(allowed) <= {'verified', 'unverified'}


def test_search_status_policy_blocks_old_override_during_recheck():
    old = offer(1, verification='deverified')
    req = profile(verification=['verified', 'deverified'])['requirements']
    result = finder.recheck(old, req, request=market([old]))
    assert result['status'] == 'requirements_changed'
    assert result['reason'] == 'verification'


def test_ranking_puts_fitting_cheap_first(tmp_path):
    p = finder.make_profile(P2_2A, 'p')
    offers = [offer(1, price=0.6), offer(2, price=0.339), offer(3, eff=32, price=0.05)]
    result = finder.find(p, request=market(offers), sleep=lambda s: None)
    assert [r['offer_id'] for r in result['rows']] == [2, 1, 3]
    assert result['rows'][2]['flags'].count('over_deadline') == 1


def config(**changes):
    return MarketConfig(max_price=1.0, request_spacing=0, **changes)


def scan(offers, **kwargs):
    return scan_market(config(), request=market(offers), sleep=lambda s: None, **kwargs)


def test_fake_endpoint_reproduces_probe3():
    # Окно с 80 предложениями, 20 из них заняты: с rentable/rented ответ < 64, но неполный.
    offers = [offer(i, price=0.9, rented=i % 4 == 0) for i in range(80)]
    request = market(offers)
    assert len(request('dph_total>=0.8 dph_total<=1', config())) == 64
    assert len(request('rentable=true rented=false dph_total>=0.8 dph_total<=1', config())) == 48


def test_price_scan_finds_everything_behind_post_filters():
    offers = [offer(i, price=round(0.001*((i*37) % 300+1), 6), rented=i % 3 == 0,
                    cpu_cores_effective=8 if i % 5 == 0 else 64) for i in range(300)]
    result = scan(offers)
    assert result['complete'] and len(result['offers']) == 300
    assert all('rent' not in q['query'] and 'cpu' not in q['query'] for q in result['queries'])
    assert any(str(q.get('continued')).startswith('split_into_') for q in result['queries'])


def test_initial_windows_do_not_merge_on_repeated_prices():
    prices = [0.1]*100 + [round(0.2+0.001*i, 4) for i in range(300)] + [0.5]*200
    windows = initial_windows(prices, 1.0, per_window=50)
    assert len(windows) >= 7
    for low, high in windows:
        assert sum(1 for p in prices if low <= p <= high) <= 200


def test_prior_prices_cut_queries():
    offers = [offer(i, price=round(0.001*((i*37) % 300+1), 6)) for i in range(300)]
    cold = scan(offers)
    warm = scan(offers, prior_prices=[o['dph_base'] for o in offers])
    assert warm['complete'] and len(warm['offers']) == 300
    assert len(warm['queries']) < len(cold['queries'])
    windows = initial_windows([0.1]*40+[0.2]*40, 1.0)
    assert len(windows) == 2 and windows[0][1] == pytest.approx(0.15) and windows[-1][1] == 1.0


def test_price_scan_reports_ties_budget_and_access_errors():
    tie = [offer(i, price=0.5) for i in range(70)]
    result = scan(tie)
    assert not result['complete'] and result['incomplete'][0]['reason'] == 'price_tie_over_page'
    offers = [offer(i, price=round(0.001*((i*37) % 300+1), 6)) for i in range(300)]
    result = scan(offers, max_queries=2)
    assert not result['complete'] and result['incomplete'][0]['reason'] == 'request_or_time_budget'

    def limited(query, cfg):
        raise SearchError('rate_limited', 429)
    result = scan_market(config(), request=limited, sleep=lambda s: None)
    assert not result['complete'] and result['queries'][0]['status'] == 'rate_limited'


def test_price_scan_retries_timeout_once():
    calls = []
    good = market([offer(1)])

    def flaky(query, cfg):
        calls.append(query)
        if len(calls) == 1:
            raise SearchError('timeout')
        return good(query, cfg)
    result = scan_market(config(), request=flaky, sleep=lambda s: None)
    assert result['complete'] and len(calls) == 2 and result['queries'][0]['continued'] == 'retry_after_timeout'


def test_price_window_uses_base_price_and_storage_price_is_recomputed():
    item = offer(1, price=1.005)   # dph_total выше предела, база 0.995 — в окне
    assert [o['id'] for o in scan([item])['offers']] == [1]
    assert finder.evaluate(item, profile(), catalog())[1] == 'price'
    priced = offer(2, dph_base=0.5333333333333333, storage_cost=0.2666666666666666, dph_total=0.5407)
    assert finder.price_for_storage(priced, 60) == pytest.approx(0.5555555555555556)
    row, _ = finder.evaluate(priced, profile(disk_gb=60), catalog())
    assert row['usd_per_hour'] == pytest.approx(0.5555555555555556)


def busy_market(n=1500, seed=5):
    import random
    rng = random.Random(seed)
    offers = []
    for i in range(n):
        price = rng.choice([0.10666667, 0.13333333, 0.16]) if rng.random() < 0.15 else round(min(0.999, rng.expovariate(4)), 4)
        offers.append(offer(i, price=price, rented=rng.random() < 0.85, num_gpus=rng.choice([1, 1, 2, 4]),
                            cpu_cores_effective=rng.choice([16, 24, 32, 64, 128])))
    return offers


def test_sweep_unions_pass_shapes_and_finds_hidden_offers():
    offers = busy_market()
    available = {o['id'] for o in offers if not o['rented']}
    result = finder.sweep_collection(profile(min_effective_cpus=16), request=market(offers, lossy=True),
                                     sleep=lambda s: None)
    found = {o['id'] for o in result['offers']}
    names = [p['name'] for p in result['passes']]
    assert names == ['price', 'exclude', 'vcpu_bands', 'gpu_count'] and not result['complete']
    assert len(found & available) >= 0.9*len(available)
    assert result['passes'][1]['new'] > 0          # досмотр с исключением раскрывает скрытое
    assert all('rentable=true rented=false' in q['query'] for q in result['queries'])


def test_sweep_respects_query_budget():
    calls = []
    result = finder.sweep_collection(profile(min_effective_cpus=16),
                                     request=market(busy_market(), calls=calls, lossy=True),
                                     sleep=lambda s: None, max_queries=5)
    assert len(calls) <= 5 and result['incomplete']


def test_find_reports_sweep_and_flags(tmp_path):
    result = finder.find(profile(min_effective_cpus=16), request=market(busy_market(300)), sleep=lambda s: None)
    assert result['search']['mode'] == 'sweep' and result['search']['last_two_passes_new_fraction'] is not None
    assert 'поиск до насыщения' in finder.console_table(result)


def test_recheck_statuses():
    offers = [offer(1), offer(2, machine_id=1001, rented=True)]
    assert recheck(1, 1001, config(), request=market(offers))['status'] == 'available'
    gone = recheck(2, 1001, config(), request=market(offers))
    assert gone['status'] == 'gone' and gone['reason'] == 'not_rentable'

    def broken(query, cfg):
        raise SearchError('timeout')
    assert recheck(1, 1001, config(), request=broken)['status'] == 'error'
    check = finder.recheck(dict(offer_id=1, machine_id=1001), dict(max_hourly_usd=0.2, disk_gb=60),
                           request=market(offers))
    assert check['status'] == 'price_changed'


def test_repository_profiles_and_validation():
    assert {'p2_2a', 'p2_1', 'all'} <= set(finder.list_profiles())
    for name in finder.list_profiles():
        finder.load_profile(name)
    with pytest.raises(ValueError):
        finder.make_profile(dict(requirements=dict(typo=1)), 'bad')
    with pytest.raises(ValueError):
        finder.make_profile(dict(rank_by='cost'), 'bad')
    with pytest.raises(ValueError):
        finder.make_profile(dict(workload=dict(core_hours=[5, 1])), 'bad')


def test_vast_find_writes_page_offers_and_history(tmp_path):
    vf = load('vast_find')
    offers = [offer(1), offer(2, cpu='Intel(R) Core(TM) i9-14900K', eff=32, host=32, price=0.15)]
    result, page = vf.run('all', output=tmp_path, request=market(offers), quiet=True, sleep=lambda s: None)
    vf.run('all', output=tmp_path, request=market(offers), quiet=True, sleep=lambda s: None)
    text = page.read_text(encoding='utf-8')
    assert '<script src' not in text and 'https://' not in text and '"offer_id": 1' in text
    document = json.loads((tmp_path/'all/offers.json').read_text(encoding='utf-8'))
    first = document['offers'][0]
    assert {'id', 'machine_id', 'dph_total', 'cpu_name', 'cpu_cores_effective', 'cpu_ram'} <= set(first)
    assert first['finder']['rank'] == 1
    history = finder.History(tmp_path/'history.sqlite3')
    try:
        rows = history.annotate([dict(offer_id=1, machine_id=1001)])
    finally:
        history.close()
    assert rows[0]['history']['offer_runs_seen'] == 2


def test_vast_find_replays_monitor_snapshot(tmp_path):
    vf = load('vast_find')
    snapshot = tmp_path/'000001.json.gz'
    with gzip.open(snapshot, 'wt', encoding='utf-8') as stream:
        json.dump(dict(collection=dict(offers=[offer(1), offer(2, resource_type='disk', num_gpus=0)])), stream)
    result, _ = vf.run('p2_2a', replay=snapshot, output=tmp_path/'out', quiet=True)
    assert [r['offer_id'] for r in result['rows']] == [1] and result['rejected'] == {'disk_contract': 1}
    assert result['search']['strategy'] == 'replay'


def p2_approval(tmp_path, **changes):
    value = dict(approved=True, user_message='да', budget_usd=20, max_hourly_usd=0.5, max_hours=24,
                 min_effective_cpus=32, cpu_name_regex='EPYC', max_rent_attempts=2, min_ram_gb=32,
                 disk_gb=60, min_reliability=0.95, allow_gpu=True)
    value.update(changes)
    path = tmp_path/'approval.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def test_vast_p2_offers_include_gpu_instances_only_when_allowed(tmp_path, monkeypatch):
    vast = load('vast_p2')
    monkeypatch.setattr(vast, 'WORK', tmp_path)
    monkeypatch.setattr(vast, 'APPROVAL', p2_approval(tmp_path))
    found = vast.search_offers(request=market([offer(1)]), sleep=lambda s: None)
    saved = json.loads((tmp_path/'offers.json').read_text(encoding='utf-8'))
    assert [o['id'] for o in found] == [1] and saved['cpu_name_regex'] == 'EPYC'
    monkeypatch.setattr(vast, 'APPROVAL', p2_approval(tmp_path, allow_gpu=False))
    assert vast.search_offers(request=market([offer(1)]), sleep=lambda s: None) == []


def test_vast_p2_profile_from_approval_keeps_approval_limits(tmp_path):
    vast = load('vast_p2')
    plan = json.loads(p2_approval(tmp_path, profile='p2_2a', max_hourly_usd=0.45).read_text(encoding='utf-8'))
    p = vast.search_profile(plan)
    assert p['workload']['critical_path_hours'] == (12.0, 13.0)
    assert p['requirements']['max_hourly_usd'] == 0.45 and p['requirements']['allow_gpu'] is True


def test_vast_p2_rent_skips_offer_taken_meanwhile(tmp_path):
    vast = load('vast_p2')
    plan = json.loads(p2_approval(tmp_path).read_text(encoding='utf-8'))
    listed = [offer(1), offer(2)]
    live = market([offer(1, rented=True), offer(2, dph_total=0.3)])
    chosen = vast._first_still_available([dict(o) for o in listed], plan, request=live)
    assert chosen['id'] == 2 and chosen['dph_total'] == 0.3
    with pytest.raises(RuntimeError):
        vast._first_still_available([dict(listed[0])], plan, request=live)


def test_skipped_passes_are_not_saturation():
    result = finder.sweep_collection(profile(), request=market([offer(1)]),
                                     sleep=lambda _: None, max_queries=1)
    assert result['last_two_passes_new_fraction'] is None
    assert not result['saturated']
    assert any(p['status'] == 'skipped' for p in result['passes'])


def test_slow_queries_leave_budget_for_diversification():
    now, calls = [0.], []

    def slow(query, cfg):
        calls.append((query, cfg.timeout))
        now[0] += cfg.timeout
        raise SearchError('timeout')

    result = finder.sweep_collection(profile(), request=slow, clock=lambda: now[0],
                                     sleep=lambda seconds: now.__setitem__(0, now[0]+seconds),
                                     max_seconds=60.)
    assert result['elapsed_seconds'] <= 60.+1e-8
    assert all(0 < timeout <= 15 for _, timeout in calls)
    assert all(p['queries'] > 0 for p in result['passes'])
    assert result['last_two_passes_new_fraction'] is None and not result['saturated']


def test_access_error_stops_all_passes():
    calls = []

    def limited(query, cfg):
        calls.append(query)
        raise SearchError('rate_limited', 429)

    result = finder.sweep_collection(profile(), request=limited, sleep=lambda _: None)
    assert len(calls) == 1 and result['stop_reason'] == 'access_error'
    assert all(p['status'] == 'skipped' for p in result['passes'][1:])


@pytest.mark.parametrize('changes', [
    dict(cpu_ram=1000), dict(verification='unverified'), dict(cpu_arch=None),
    dict(cpu_cores_effective=32), dict(resource_type='disk'), dict(direct_port_count=0),
    dict(reliability=0.1), dict(cpu_name='Intel Xeon E5-2680 v4'),
])
def test_rental_rechecks_every_requirement(tmp_path, changes):
    vast = load('vast_p2')
    plan = json.loads(p2_approval(tmp_path, min_share=0.95, verification=['verified']).read_text(encoding='utf-8'))
    with pytest.raises(RuntimeError):
        vast._first_still_available([offer(1)], plan, request=market([offer(1, **changes)]))


def test_rental_refuses_failed_recheck(tmp_path):
    vast = load('vast_p2')
    plan = json.loads(p2_approval(tmp_path).read_text(encoding='utf-8'))

    def broken(query, cfg):
        raise SearchError('timeout')

    with pytest.raises(RuntimeError, match='аренда запрещена'):
        vast._first_still_available([offer(1)], plan, request=broken)


def test_recheck_uses_storage_price_and_identity():
    priced = offer(1, dph_total=0.1, dph_base=0.2, storage_cost=12.)
    got = finder.recheck(offer(1), dict(max_hourly_usd=0.5, disk_gb=60), request=market([priced]))
    assert got['status'] == 'price_changed'
    got = recheck(1, 1001, config(), request=lambda q, c: [offer(1, machine_id=999)])
    assert got['status'] == 'error' and got['reason'] == 'machine_identity_mismatch'


def test_narrow_profile_also_runs_relaxed_control():
    calls = []
    result = finder.sweep_collection(finder.load_profile('p2_2a_station'),
                                     request=market([offer(1)], calls=calls), sleep=lambda _: None)
    assert 'relaxed' in [p['name'] for p in result['passes']]
    assert any('cpu_cores_effective>=16 disk_space>=20' in q for q in calls)
    assert len(finder.find(finder.load_profile('p2_2a_station'), offers=result['offers'])['rows']) == 1


def test_prepare_p2_2a_creates_only_local_package_and_command_templates(tmp_path, monkeypatch):
    prep = load('prepare_p2_2a')
    root = tmp_path/'project'
    (root/'scripts').mkdir(parents=True)
    (root/'scripts/vast_p2.py').write_text(
        'def prepare():\n    WORK.mkdir(parents=True)\n    BUNDLE.write_bytes(b"test")\n', encoding='utf-8')
    monkeypatch.setattr(prep, 'ROOT', root)
    output = prep.prepare(root/'scratch/new_run')
    commands = json.loads((output/'commands.json').read_text(encoding='utf-8'))
    assert commands['approved'] is False and commands['rental_enabled'] is False
    assert commands['expected_jobs'] == 1152*2*4+144*2 == 9504
    assert 'scripts/admission_p2_2.py' in commands['full']
    assert '--expect-full' in commands['accept']
    assert not (output/'approval.json').exists()
    with pytest.raises(ValueError):
        prep.prepare(output)
    with pytest.raises(ValueError):
        prep.prepare(root/'outside_scratch')


def test_monitor_is_bounded_and_preserves_snapshots(tmp_path):
    vf = load('vast_find')
    now = [0.]
    result, folder = vf.monitor('p2_2a_station', cycles=3, interval=30, session_seconds=40,
                               output=tmp_path, max_seconds=10, request=market([offer(1)]), quiet=True,
                               clock=lambda: now[0], sleep=lambda sec: now.__setitem__(0, now[0]+sec))
    session = json.loads((folder/'session.json').read_text(encoding='utf-8'))
    assert 1 <= len(session['snapshots']) < 3 and now[0] <= 40+1e-8
    assert not session['rental_enabled']
    assert len(list(folder.glob('*_offers.json'))) == len(session['snapshots'])
    assert result['profile']['name'] == 'p2_2a_station'


def test_station_selection_is_only_proposal_and_prefers_verified():
    from datetime import datetime, timezone
    selector = load('vast_station_select')
    doc = dict(utc=datetime.now(timezone.utc).isoformat(), offers=[
        offer(1, verification='unverified', price=0.2), offer(2, price=0.3)])
    result = selector.select(doc, request=market(doc['offers']))
    assert result['recommended_offer_id'] == 2
    assert result['approved'] is False and result['rental_enabled'] is False
    assert result['candidates'][0]['max_compute_storage_usd']+2.4 <= 12
    unverified = next(c for c in result['candidates'] if c['row']['offer_id'] == 1)
    assert 'unverified' in unverified['risks'] and 'deverified' not in unverified['risks']


def test_station_selection_keeps_fast_higher_hourly_offer_with_bounded_total():
    from datetime import datetime, timezone
    selector = load('vast_station_select')
    fast = offer(1, cpu='AMD Ryzen Threadripper PRO 7995WX 96-Cores', price=0.52)
    result = selector.select(dict(utc=datetime.now(timezone.utc).isoformat(), offers=[fast]), request=market([fast]))
    item = result['candidates'][0]
    assert item['row']['usd_per_hour'] > 0.4 and item['max_hours'] < 24
    assert item['max_compute_storage_usd']+item['transfer_reserve_usd'] <= 12
    assert item['run_deadline_hours']+1.5 == item['max_hours']


def test_station_selection_refuses_stale_and_failed_recheck():
    selector = load('vast_station_select')
    doc = dict(utc='2020-01-01T00:00:00+00:00', offers=[offer(1)])
    with pytest.raises(ValueError, match='свежий снимок'):
        selector.select(doc)
    assert selector.select(doc, offline=True)['status'] == 'offline_replay'
    from datetime import datetime, timezone
    doc['utc'] = datetime.now(timezone.utc).isoformat()

    def broken(q, c):
        raise SearchError('timeout')

    result = selector.select(doc, request=broken)
    assert result['status'] == 'no_candidate' and result['checks'][0]['status'] == 'error'


def test_cpu_matrix_fetch_uses_shared_recheck(monkeypatch):
    matrix = load('vast_cpu_matrix')
    monkeypatch.setattr(market_search, 'anonymous_request', market([offer(1)]))
    assert [o['id'] for o in matrix.fetch_offers(dict(offer_id=1, machine_id=1001))] == [1]
    assert matrix.fetch_offers(dict(offer_id=5, machine_id=1001)) == []


def test_saturation_counts_only_relevant_offers():
    """W2-T019: доля новых — по подходящим по vCPU/диску; меньшие машины из relaxed не раздувают знаменатель."""
    small = [offer(i, eff=16, host=16, price=round(0.01+0.001*i, 4)) for i in range(200)]
    big = [offer(1000+i, eff=64, host=64, price=round(0.3+0.001*i, 4)) for i in range(20)]
    result = finder.sweep_collection(profile(min_effective_cpus=64, disk_gb=60), request=market(small+big),
                                     sleep=lambda _: None)
    found = {o['id']: o for o in result['offers']}
    assert any(o['cpu_cores_effective'] == 16 for o in found.values())   # relaxed принёс малые
    assert result['relevant_offers'] == sum(1 for o in found.values() if o['cpu_cores_effective'] >= 64)
    assert all('new_relevant' in p and p['new_relevant'] <= p['new'] for p in result['passes'])
    relaxed = next(p for p in result['passes'] if p['name'] == 'relaxed')
    assert relaxed['new'] > relaxed['new_relevant']


def test_undrained_exclude_is_not_saturation():
    result = finder.sweep_collection(profile(min_effective_cpus=16), request=market(busy_market(), lossy=True),
                                     sleep=lambda _: None, drain_rounds=1)
    exclude = next(p for p in result['passes'] if p['name'] == 'exclude')
    assert exclude['status'] == 'partial' and 'drain_round_limit' in exclude['reasons']
    assert result['exclude_drained'] is False and not result['saturated']
    assert result['stop_reason'] != 'saturation_heuristic'
