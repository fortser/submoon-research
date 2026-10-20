"""Радар: только синтетический API, без аренды и без внешней сети."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import threading
import urllib.error
import urllib.request

import pytest
import yaml

from submoon_research.compute import finder
from submoon_research.compute.market_search import SearchError
from submoon_research.compute.radar import (
    AlreadyRunning, Radar, Store, allocation_key, assess, load_config, single_instance,
)
from submoon_research.compute.radar_app import control, make_server


NOW = 1792054800.


def offer(i=47034418, machine=143224, **kwargs):
    result = dict(id=i, machine_id=machine, resource_type='gpu', num_gpus=1,
                  gpu_name='RTX PRO 5000', cpu_name='AMD Ryzen Threadripper PRO 9975WX 32-Cores',
                  cpu_arch='amd64', cpu_cores=64, cpu_cores_effective=64, cpu_ram=257061,
                  disk_space=1000, dph_base=.6, storage_cost=.1333333333333333,
                  dph_total=.611111111111, reliability=.995, verification='unverified',
                  direct_port_count=99, rentable=True, rented=False)
    result.update(kwargs)
    return result


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def store(tmp_path):
    db = Store(tmp_path/'data', now=NOW)
    yield db
    db.close()


def radar(cfg, store, tmp_path, offers=None, notify=None, wall=None):
    def request(query, config):
        mid = int(query.split('machine_id=')[-1])
        return [o for o in offers or [] if o['machine_id'] == mid]
    return Radar(cfg, store, tmp_path/'findings.md', request=request, notify=notify,
                 wall=wall or (lambda: NOW), sleep=lambda s: None)


def empty_search(monkeypatch, offers=()):
    def find(profile, **kwargs):
        rows = [finder.evaluate(o, profile, finder.Catalog.load())[0] for o in offers]
        return dict(raw={o['id']: o for o in offers}, rows=rows,
                    search=dict(errors=[], receipts=[dict(status='ok')], complete=False, passes=[]))
    monkeypatch.setattr(finder, 'find', find)


def test_target_estimate_and_conservative_reserve(cfg):
    row, reason = assess(offer(), cfg)
    assert reason is None
    assert row['kind'] == 'target' and row['estimate']['workers'] == 32
    assert row['usd_per_hour'] == pytest.approx(.611111111111)
    assert 14 < row['estimate']['hours'][1] < 15
    assert row['estimate_total_usd'] == pytest.approx(row['estimate']['usd'][1]+2*row['usd_per_hour']+1)
    assert row['estimate_total_usd'] < 13


@pytest.mark.parametrize('changes,reason', [
    ({'rentable': False}, 'unavailable'),
    ({'rented': True}, 'unavailable'),
    ({'verification': 'deverified'}, 'verification'),
    ({'verification': None}, 'verification'),
    ({'resource_type': 'disk', 'num_gpus': 0}, 'disk_contract'),
    ({'machine_id': 139652}, 'excluded_machine'),
    ({'dph_base': .7}, 'target_price'),
    ({'storage_cost': None}, 'unknown_storage_price'),
    ({'cpu_ram': None}, 'ram'),
    ({'cpu_cores_effective': 32}, 'vcpu'),
    ({'cpu_cores': 128}, 'share'),
    ({'cpu_name': 'QEMU Virtual CPU'}, 'unknown_cpu'),
    ({'reliability': .95}, 'reliability'),
    ({'cpu_name': 'AMD Ryzen Threadripper PRO 3995WX'}, 'series_time'),
])
def test_rejections(cfg, changes, reason):
    assert assess(offer(**changes), cfg)[1] == reason


def test_analog_uses_workload_not_raw_core_count(cfg):
    o = offer(cpu_name='AMD Ryzen Threadripper PRO 7995WX 96-Cores', cpu_cores=192,
              cpu_cores_effective=192, dph_base=.5, verification='verified')
    row, reason = assess(o, cfg)
    assert reason is None and row['kind'] == 'analog'
    assert row['estimate']['workers'] == 96
    # Большое количество ядер не устраняет последовательный критический путь.
    assert row['estimate']['hours'][1] > 14


@pytest.mark.parametrize('key,value', [
    ('interval_seconds', 60), ('automatic_rental', True), ('trial_budget_usd', 2),
    ('trial_hours', 1), ('max_paid_attempts', 2), ('paid_retries', 1),
    ('full_run_budget_usd', 14), ('max_hourly_usd', 1), ('port', 80),
    ('request_timeout_seconds', float('nan')), ('max_recheck_machines', 1.5),
])
def test_config_cannot_widen_approval(tmp_path, cfg, key, value):
    cfg[key] = value
    path = tmp_path/'config.yaml'
    path.write_text(yaml.safe_dump(cfg), encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)


def test_single_episode_survives_restart_and_offer_id_change(cfg, store, tmp_path):
    sounds = []
    r = radar(cfg, store, tmp_path, [offer()], notify=lambda: sounds.append(1))
    r.probe(143224)
    assert len(sounds) == 1 and len(store.recent_events()) == 1
    r2 = radar(cfg, store, tmp_path, [offer(i=777)], notify=lambda: sounds.append(1))
    r2.probe(143224)
    assert len(sounds) == 1 and len(store.recent_events()) == 1
    assert store.rows()[0]['offer_id'] == 777
    assert '777' in r.findings.read_text(encoding='utf-8')


def test_state_really_persists_on_disk(cfg, tmp_path):
    folder = tmp_path/'persist'
    db = Store(folder, now=NOW)
    r = radar(cfg, db, tmp_path, [offer()])
    r.probe(143224)
    db.close()
    other = Store(folder, now=NOW+5)
    try:
        row, reason = assess(offer(i=99), cfg)
        assert other.observe(offer(i=99), row, reason, NOW+5) is False
        assert other.get('started_epoch') == NOW
    finally:
        other.close()


def test_negative_rearms_but_absence_and_error_do_not(cfg, store, tmp_path):
    row, reason = assess(offer(), cfg)
    assert store.observe(offer(), row, reason, NOW)
    r = radar(cfg, store, tmp_path, [])
    r.probe(143224)
    assert store.rows()[0]['state'] == 'unknown'
    assert not store.observe(offer(), row, None, NOW+1)
    unavailable = offer(rentable=False)
    row_no, reason_no = assess(unavailable, cfg)
    store.observe(unavailable, row_no, reason_no, NOW+2)
    assert store.observe(offer(), row, None, NOW+3)
    assert len(store.recent_events()) == 2


def test_network_failure_preserves_episode(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer()])
    r.probe(143224)
    def fail(*args):
        raise SearchError('timeout')
    r.transport = fail
    assert r.probe(143224)['status'] == 'error'
    assert store.rows()[0]['state'] == 'unknown'
    r.transport = lambda *args: [offer()]
    r.probe(143224)
    assert len(store.recent_events()) == 1


def test_unknown_price_does_not_rearm(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer()])
    r.probe(143224)
    r.transport = lambda *args: [offer(storage_cost=None)]
    r.probe(143224)
    r.transport = lambda *args: [offer()]
    r.probe(143224)
    assert len(store.recent_events()) == 1


def test_price_hysteresis_avoids_sound_on_small_threshold_fluctuations(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer()])
    r.probe(143224)
    r.transport = lambda *args: [offer(dph_base=.645)]  # с диском чуть выше $0.65
    r.probe(143224)
    r.transport = lambda *args: [offer()]
    r.probe(143224)
    assert len(store.recent_events()) == 1
    r.transport = lambda *args: [offer(dph_base=.71)]  # >5% выше порога
    r.probe(143224)
    r.transport = lambda *args: [offer()]
    r.probe(143224)
    assert len(store.recent_events()) == 2


def test_target_probe_discovers_changed_ids_and_prefers_available(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer(rentable=False), offer(i=777)])
    assert r.probe(143224)['status'] == 'checked'
    assert len(store.rows()) == 1
    assert store.rows()[0]['offer_id'] == 777
    assert store.rows()[0]['state'] == 'available'


def test_target_is_not_dependent_on_broad_search_and_saved_before_sound(cfg, store, tmp_path, monkeypatch):
    def sound():
        assert '47034418' in (tmp_path/'findings.md').read_text(encoding='utf-8')
        assert len(store.recent_events()) == 1
    r = radar(cfg, store, tmp_path, [offer()], notify=sound)
    empty_search(monkeypatch)
    r.poll()
    assert len(store.recent_events()) == 1
    assert list((store.folder/'snapshots').glob('*.gz'))


def test_recheck_can_reject_a_broad_candidate(cfg, store, tmp_path, monkeypatch):
    empty_search(monkeypatch, [offer(i=88, machine=900)])
    r = radar(cfg, store, tmp_path, [offer(i=88, machine=900, rentable=False)])
    r.poll()
    assert not store.recent_events()
    assert store.rows()[0]['state'] == 'unavailable'


def test_multiple_findings_one_sound_per_cycle(cfg, store, tmp_path, monkeypatch):
    offers = [offer(), offer(i=88, machine=900)]
    empty_search(monkeypatch, offers)
    sounds = []
    r = radar(cfg, store, tmp_path, offers, notify=lambda: sounds.append(1))
    r.poll()
    assert len(store.recent_events()) == 2 and len(sounds) == 1
    r.poll()
    assert len(sounds) == 1


def test_mute_and_snooze_do_not_lose_findings(cfg, store, tmp_path):
    control(radar(cfg, store, tmp_path), 'sound')
    sounds = []
    r = radar(cfg, store, tmp_path, [offer()], notify=lambda: sounds.append(1))
    r.probe(143224)
    assert len(store.recent_events()) == 1 and sounds == []
    control(r, 'sound')
    control(r, 'snooze')
    r.transport = lambda *args: [offer(i=88, machine=900)]
    r.probe(900)
    assert len(store.recent_events()) == 2 and sounds == []


def test_sound_failure_does_not_lose_result(cfg, store, tmp_path):
    def fail():
        raise OSError('no sound')
    r = radar(cfg, store, tmp_path, [offer()], notify=fail)
    r.probe(143224)
    assert store.get('notification_error') == 'sound_unavailable'
    assert store.recent_events()


def test_review_five_calendar_days_no_budget_increase(cfg, store, tmp_path):
    original = deepcopy(cfg)
    r = radar(cfg, store, tmp_path, wall=lambda: NOW+5*86400)
    assert r.view()['review_required']
    row, reason = assess(offer(), cfg)
    store.observe(offer(), row, reason, NOW+1)
    assert not r.view()['review_required']
    assert cfg == original


def test_staleness(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer()])
    r.probe(143224)
    r.wall = lambda: NOW+1801
    assert r.view()['rows'][0]['stale']


def test_secret_fields_not_saved(cfg, store, tmp_path):
    r = radar(cfg, store, tmp_path, [offer(instance_api_key='NEVER-SAVE', api_key='SECRET')])
    r.probe(143224)
    assert 'NEVER-SAVE' not in json.dumps(r.view())
    assert 'SECRET' not in json.dumps(r.view())


def test_access_error_stops_further_provider_requests(cfg, store, tmp_path):
    calls = []
    r = radar(cfg, store, tmp_path)
    def fail(query, config):
        calls.append(query)
        raise SearchError('rate_limited')
    r.transport = fail
    r.poll()
    assert len(calls) == 1
    assert r.view()['status']['next_poll_epoch'] == NOW+1800


def test_exclusive_lock_and_recovery(tmp_path):
    with single_instance(tmp_path):
        with pytest.raises(AlreadyRunning):
            with single_instance(tmp_path):
                pass
    with single_instance(tmp_path):
        pass


def test_allocation_not_price_or_offer_identity():
    assert allocation_key(offer()) == allocation_key(offer(i=77, dph_base=.1))
    assert allocation_key(offer()) != allocation_key(offer(cpu_cores_effective=32))


def test_local_server_csrf_and_no_poll_from_ui(cfg, store, tmp_path):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    sock.close()
    r = radar(cfg, store, tmp_path)
    server = make_server(r, port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{port}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url+'/') as response:
            html = response.read().decode()
        token = html.split("const TOKEN='")[1].split("'")[0]
        for _ in range(2):
            with opener.open(url+'/api/state') as response:
                assert json.load(response)['paid_actions'] == 0
        assert r.count == 0
        bad = urllib.request.Request(url+'/control', data=b'{"action":"stop"}',
                                     headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as e:
            opener.open(bad)
        assert e.value.code == 403 and not r.stop.is_set()
        good = urllib.request.Request(url+'/control', data=b'{"action":"pause"}',
            headers={'Content-Type': 'application/json', 'X-Radar-Token': token})
        with opener.open(good) as response:
            assert response.status == 200
        assert store.get('paused')
        evil = urllib.request.Request(url+'/control', data=b'{"action":"stop"}',
            headers={'Content-Type': 'application/json', 'X-Radar-Token': token, 'Origin': 'https://evil.example'})
        with pytest.raises(urllib.error.HTTPError):
            opener.open(evil)
        assert not r.stop.is_set()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_actual_hardware_estimate_limits_workers_and_never_approves():
    import importlib.util
    path = Path(__file__).resolve().parents[2]/'scripts/vast_trial_estimate.py'
    spec = importlib.util.spec_from_file_location('trial_estimate', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    hardware = dict(cpu_name=offer()['cpu_name'], physical_workers=32,
                    effective_vcpus=64, ram_gb=257, core_speed_ratio=None)
    prediction = module.predict(hardware, .611111, .1)
    assert prediction['within_budget'] and prediction['enough_workers']
    assert prediction['continuation_approved'] is False
    hardware['effective_vcpus'] = 16
    prediction = module.predict(hardware, .611111)
    assert prediction['estimate']['workers'] == 16
    assert not prediction['enough_workers'] and not prediction['within_budget']
    hardware['ram_gb'] = None
    with pytest.raises(ValueError):
        module.predict(hardware, .6)
    hardware.update(ram_gb=257, effective_vcpus=64, cpu_name='Unknown CPU')
    with pytest.raises(ValueError):
        module.predict(hardware, .6)
    hardware['core_speed_ratio'] = .95
    assert module.predict(hardware, .6)['speed_confidence'] == 'measured'


def test_radar_has_no_paid_executor_imports():
    root = Path(__file__).resolve().parents[2]
    for name in ('radar.py', 'radar_app.py'):
        source = (root/'src/submoon_research/compute'/name).read_text(encoding='utf-8')
        assert 'import vast_p2' not in source and 'create instance' not in source
