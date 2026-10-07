"""Охват CPU, ложные совпадения, частичная выдача, ошибки и история."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from submoon_research.compute.cpu_targets import parse_targets
from submoon_research.compute.market_search import (
    MarketConfig, SearchError, parse_response, collect, rejection, candidate, cli_request,
)
from submoon_research.compute.market_store import MarketStore

ROOT = Path(__file__).resolve().parents[2]


def targets():
    return parse_targets((ROOT/'good_cpu.txt').read_text(encoding='utf-8'))


EXACT_MODELS = [
    'Core i9-14900K', 'Core i9-14900KF', 'Core i9-14900KS',
    'Core i9-13900K', 'Core i9-13900KF', 'Core i9-13900KS',
    'Core i9-12900K', 'Core i9-12900KF',
    'Core i7-14700', 'Core i7-14700K', 'Core i7-14700KF', 'Core i7-13700K', 'Core i7-13700KF',
    'Core Ultra 9 285K', 'Core Ultra 7 265K',
    'Ryzen 9 9950X', 'Ryzen 9 9950X3D', 'Ryzen 9 9900X', 'Ryzen 9 7950X',
    'Ryzen 9 7950X3D', 'Ryzen 9 7900X', 'Ryzen 9 7900',
    'Ryzen 7 9700X', 'Ryzen 7 9800X3D', 'Ryzen 7 7700X',
    'Ryzen 9 5950X', 'Ryzen 9 5900X', 'Ryzen 9 5900XT',
    'Threadripper 7960X', 'Threadripper 7970X', 'Threadripper 7980X',
    'Threadripper PRO 7955WX', 'Threadripper PRO 7975WX', 'Threadripper PRO 7985WX',
    'Threadripper PRO 7995WX', 'Threadripper PRO 5955WX', 'Threadripper PRO 5965WX', 'Threadripper PRO 5975WX',
    'EPYC 9575F', 'EPYC 9475F', 'EPYC 9375F', 'EPYC 9474F', 'EPYC 9374F', 'EPYC 9274F', 'EPYC 9174F',
    'EPYC 75F3', 'EPYC 73F3', 'EPYC 7543', 'EPYC 7443', 'EPYC 7343',
    'EPYC 4584PX', 'EPYC 4564P', 'EPYC 4484PX', 'EPYC 9654', 'EPYC 9554', 'EPYC 9555',
]


@pytest.mark.parametrize('model', EXACT_MODELS)
def test_every_explicit_cpu(model):
    rules = targets()
    assert any(t.matches(model) for t in rules), model
    messy = ('Intel(R) '+model.replace('Core ', 'Core(TM) ')) if model.startswith('Core') else 'AMD '+model
    assert any(t.matches(messy.lower()+' 32-Core Processor') for t in rules)


@pytest.mark.parametrize('model', ['Intel Xeon w5-2465X', 'Intel Xeon W-2455',
                                  'Intel Xeon w9-3495X', 'AMD EPYC 4464P', 'AMD EPYC 9655'])
def test_families(model):
    assert any(t.matches(model) for t in targets())


@pytest.mark.parametrize('model', ['Core i9-14900', 'Core i9-14900KX', 'Ryzen 9 79000X',
                                  'Xeon E5-2400', 'EPYC 4490', 'EPYC 9395F', 'EPYC 2026'])
def test_no_unrequested_prefix_or_comment_number(model):
    assert not any(t.matches(model) for t in targets())


def offer(**changes):
    return dict(id=1, machine_id=10, resource_type='gpu', cpu_name='AMD EPYC 9654 96-Core Processor',
        cpu_cores_effective=48., cpu_cores=192, cpu_ram=64000, num_gpus=1, gpu_name='RTX 4090',
        disk_space=200., dph_total=.5, dph_base=.49, storage_total_cost=.01,
        rentable=True, rented=False, geolocation='PL') | changes


def test_partial_cpu_quota_null_ram_and_price():
    cfg = MarketConfig()
    for share in (192, 96, 48):
        o = offer(cpu_cores_effective=share)
        assert rejection(o, targets(), cfg) is None
        assert candidate(o, targets(), cfg)['cpu_share_reported'] == share/192
    assert rejection(offer(), targets(), replace(cfg, whole_machine_only=True)) == 'shared_cpu_excluded_by_scope'
    row = candidate(offer(cpu_ram=0), targets(), cfg)
    assert row['ram_gb_reported'] is None and row['physical_cores'] is None
    assert candidate(offer(), targets(), cfg)['ram_gb_reported'] == 64
    for kind in ('disk', None):
        assert rejection(offer(resource_type=kind), targets(), cfg) == 'disk_or_unknown_resource'
    assert rejection(offer(dph_total=1.01), targets(), cfg) == 'invalid_or_out_of_scope_dph_total'
    assert rejection(offer(dph_total=None), targets(), cfg) == 'invalid_or_out_of_scope_dph_total'


def test_canonical_model_does_not_split_vendor_spellings():
    keys = [candidate(offer(cpu_name=n), targets(), MarketConfig())['cpu_key'] for n in
            ['Intel(R) Core(TM) i9-14900KF', 'Intel i9-14900KF', 'Core i9-14900KF']]
    assert keys == ['core i9 14900kf']*3


@pytest.mark.parametrize('stdout,code,kind', [
    ('failed with error 429: https://x/?api_key=SECRET', 0, 'rate_limited'),
    ('failed with error 403: denied', 0, 'authentication_or_access_denied'),
    ('Warning: Unrecognized field: cpu_name\n[]', 0, 'cli_or_api_error'),
    ('<html>bad</html>', 0, 'invalid_json'), ('{}', 0, 'invalid_schema'),
    ('[]', 1, 'cli_or_api_error'), ('[{"dph_total":NaN}]', 0, 'invalid_json'),
])
def test_errors_do_not_leak_or_become_empty_success(stdout, code, kind):
    with pytest.raises(SearchError) as error:
        parse_response(SimpleNamespace(stdout=stdout, stderr='', returncode=code))
    assert error.value.kind == kind
    assert 'SECRET' not in str(error.value)


def test_valid_null_tail_and_secret_field_whitelist():
    proc = SimpleNamespace(stdout=json.dumps([offer() | {'instance_api_key':'SECRET'}])+'\nnull', stderr='', returncode=0)
    data = parse_response(proc)
    assert data[0]['id'] == 1 and 'instance_api_key' not in data[0]


def test_cli_avoids_broken_retry_flag(monkeypatch):
    command = []
    def run(cmd, **kwargs):
        command.extend(cmd)
        assert 'HTTP_PROXY' not in kwargs['env']
        return SimpleNamespace(stdout='[]', stderr='', returncode=0)
    monkeypatch.setattr('submoon_research.compute.market_search.subprocess.run', run)
    cli_request('num_gpus>=1', MarketConfig())
    assert '--retry' not in command
    assert '--no-default' in command and '--explain' not in command


def test_truncated_search_is_split():
    queries = []
    def request(q, c):
        queries.append(q)
        return [offer(id=1), offer(id=2)] if len(queries) == 1 else []
    result = collect(MarketConfig(limit=2, request_spacing=0), request=request)
    assert result['complete'] and len(queries) == 4
    assert 'dph_total<0.5' in queries[1] and 'dph_total>=0.5' in queries[2]
    limited = collect(MarketConfig(limit=2, max_queries=1, request_spacing=0),
        request=lambda q,c:[offer(id=1),offer(id=2)])
    assert not limited['complete'] and limited['incomplete']


def test_rate_limit_stops_subsequent_calls():
    called = []
    def request(q, c):
        called.append(q)
        raise SearchError('rate_limited', 429)
    result = collect(MarketConfig(request_spacing=0), request=request)
    assert not result['complete'] and len(called) == 1


def collection(complete=True):
    return dict(complete=complete, incomplete=[] if complete else [{'reason':'timeout'}],
                queries=[], offers=[], elapsed_seconds=.1)


def test_history_first_seen_reappearance_incomplete_gaps_and_duplicates(tmp_path):
    store = MarketStore(tmp_path)
    rules, cfg = targets(), MarketConfig()
    store.set_targets(rules)
    first = candidate(offer(), rules, cfg)
    store.append('2026-10-06T00:00:00+00:00', collection(), [first], {})
    store.append('2026-10-06T00:05:00+00:00', collection(False), [], {})
    assert store.db.execute('SELECT missing_count FROM active').fetchone()[0] == 0
    store.append('2026-10-06T00:10:00+00:00', collection(), [], {})
    store.append('2026-10-06T00:15:00+00:00', collection(), [], {})
    store.close()
    store = MarketStore(tmp_path)
    store.append('2026-10-06T00:20:00+00:00', collection(), [first], {})
    assert store.db.execute('SELECT first_seen,gone,missing_count FROM active').fetchone() == ('2026-10-06T00:00:00+00:00',0,0)
    changed = candidate(offer(dph_total=.4), rules, cfg)
    duplicate = candidate(offer(id=2,dph_total=.6), rules, cfg)
    store.append('2026-10-06T00:25:00+00:00', collection(), [changed,duplicate], {})
    events = [x[0] for x in store.db.execute('SELECT kind FROM events')]
    assert 'reappeared' in events and 'price_changed' in events and 'not_observed_in_scope' in events
    stats = store.statistics()
    assert stats['complete_polls'] == 5 and stats['incomplete_or_error_polls'] == 1
    model = stats['models']['epyc 9654']
    assert model['availability_fraction_of_complete_polls'] == 3/5
    assert model['unique_machines'] == 1 and model['price_samples_after_machine_allocation_dedup'] == 3
    assert len(stats['target_coverage']) == len(rules)
    store.close()


def test_volatile_metadata_not_configuration_change_and_read_only_stats(tmp_path):
    import hashlib
    rules, cfg = targets(), MarketConfig()
    store = MarketStore(tmp_path)
    row = candidate(offer(duration=1000), rules, cfg)
    store.append('2026-10-06T00:00:00+00:00', collection(), [row], {})
    updated = candidate(offer(duration=900,cpu_ghz=4.05,inet_up=501), rules, cfg)
    _, events = store.append('2026-10-06T00:05:00+00:00', collection(), [updated], {})
    assert not events
    store.close()
    before = hashlib.sha256((tmp_path/'market.sqlite3').read_bytes()).hexdigest()
    reader = MarketStore(tmp_path, read_only=True)
    assert reader.statistics()['complete_polls'] == 2
    reader.close()
    assert hashlib.sha256((tmp_path/'market.sqlite3').read_bytes()).hexdigest() == before


def test_monitor_session_lifecycle_and_runtime_without_network(tmp_path, monkeypatch):
    from submoon_research.compute.market_monitor import monitor
    root = tmp_path/'project'
    root.mkdir()
    (root/'good_cpu.txt').write_text((ROOT/'good_cpu.txt').read_text(encoding='utf-8'), encoding='utf-8')
    data = collection() | {'queries':[dict(status='ok')], 'offers':[offer()]}
    monkeypatch.setattr('submoon_research.compute.market_monitor.collect', lambda cfg:data)
    folder = monitor(root, MarketConfig(), cycles=1, folder=root/'session', duration_hours=.1)
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'completed' and manifest['paid_actions'] == 0
    assert not (folder/'collector.lock').exists()
    assert (folder/'code/market_monitor.py').is_file()
    assert json.loads((root/'tracking/runtime.json').read_text(encoding='utf-8-sig'))['processes'] == []
    with pytest.raises(ValueError, match='завершённую'):
        monitor(root, MarketConfig(), cycles=1, folder=folder, resume=True)


def test_runtime_json_with_bom_does_not_break_session(tmp_path, monkeypatch):
    from submoon_research.compute.market_monitor import monitor
    root = tmp_path/'project'
    (root/'tracking').mkdir(parents=True)
    (root/'good_cpu.txt').write_text((ROOT/'good_cpu.txt').read_text(encoding='utf-8'), encoding='utf-8')
    bom_runtime = dict(schema_version='1.0', updated_utc='2026-10-07T00:00:00Z', processes=[])
    (root/'tracking/runtime.json').write_text(
        json.dumps(bom_runtime, ensure_ascii=False), encoding='utf-8-sig')
    data = collection() | {'queries':[dict(status='ok')], 'offers':[offer()]}
    monkeypatch.setattr('submoon_research.compute.market_monitor.collect', lambda cfg:data)
    folder = monitor(root, MarketConfig(), cycles=1, folder=root/'session', duration_hours=.1)
    assert json.loads((folder/'manifest.json').read_text(encoding='utf-8'))['status'] == 'completed'
    written = (root/'tracking/runtime.json').read_bytes()
    assert not written.startswith(b'\xef\xbb\xbf')
    assert json.loads(written.decode('utf-8'))['processes'] == []
