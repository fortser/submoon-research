"""REST подменён функцией, SSH — синтетическим клиентом; платных запросов нет."""
import json
from pathlib import Path
import time

import pytest

from submoon_research.compute.hardware_trial import IMAGE, TrialError, safe_receipt
from submoon_research.compute.trial_transport import (
    BASE, MAX_RESPONSE, TransportError, VastAPI, bounded, measurements,
)


pytest.importorskip('requests')


class Response:
    def __init__(self, value, status=200, content=None):
        self.status_code = status
        self.content = json.dumps(value).encode() if content is None else content
        self.closed = False

    def iter_content(self, size):
        yield self.content

    def close(self):
        self.closed = True


def test_create_exact_on_demand_payload_once_no_secret_in_url_or_receipt():
    calls = []
    def request(method, url, **kw):
        calls.append((method, url, kw))
        return Response(dict(success=True, new_contract=7, instance_api_key='TOP_SECRET'))
    api = VastAPI(key='ACCOUNT_SECRET', request=request)
    result = api.create(42, label='submoon-hw-test', image=IMAGE, disk_gb=60, timeout=15)
    assert len(calls) == 1
    method, url, kw = calls[0]
    assert method == 'PUT' and url == BASE + '/asks/42/'
    assert 'ACCOUNT_SECRET' not in url
    assert kw['allow_redirects'] is False and kw['stream'] is True
    assert kw['headers']['Authorization'] == 'Bearer ACCOUNT_SECRET'
    body = kw['json']
    assert body['price'] is None and body['disk'] == 60 and body['cancel_unavail'] is True
    assert body['runtype'] == 'ssh_direc ssh_proxy'
    assert body['image'] == IMAGE
    assert result['new_contract'] == 7 and 'TOP_SECRET' not in json.dumps(result)


@pytest.mark.parametrize('status,error', [(400, 'no_such_ask'), (403, 'unavailable'),
                                         (429, 'TOP_SECRET'), (500, 'TOP_SECRET')])
def test_http_error_retains_status_but_never_retries_or_leaks(status, error):
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        return Response(dict(error=error, message='TOP_SECRET'), status)
    result = VastAPI(key='KEY', request=request).create(42, label='test', image=IMAGE, disk_gb=60, timeout=1)
    assert result['http_status'] == status and len(calls) == 1
    assert 'TOP_SECRET' not in json.dumps(result)


def test_network_exception_is_ambiguous_sanitized_and_not_retried():
    calls = []
    def request(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('TOP_SECRET in URL')
    result = VastAPI(key='KEY', request=request).create(42, label='test', image=IMAGE, disk_gb=60, timeout=1)
    assert result['error_code'] == 'transport_error'
    assert result['http_status'] is None and len(calls) == 1
    assert 'TOP_SECRET' not in json.dumps(result)


@pytest.mark.parametrize('content,code', [(b'<html>TOP_SECRET</html>', 'invalid_json'),
                                         (b'x' * (MAX_RESPONSE + 1), 'response_too_large')],
                         ids=['invalid_json', 'oversize'])
def test_bad_responses_bounded_and_sanitized(content, code):
    response = Response(None, content=content)
    api = VastAPI(key='KEY', request=lambda *a, **k: response)
    with pytest.raises(TransportError, match=code):
        api.request('GET', '/instances/')
    assert response.closed


def test_redirect_not_followed():
    response = Response(dict(error='TOP_SECRET'), status=302)
    api = VastAPI(key='KEY', request=lambda *a, **k: response)
    with pytest.raises(TransportError, match='http_error') as error:
        api.request('GET', '/instances/')
    assert error.value.status == 302


def test_targeted_search_and_account_schema():
    calls = []
    def request(method, url, **kw):
        calls.append((url, kw.get('json')))
        if url.endswith('/bundles/'):
            return Response(dict(offers=[dict(id=42, machine_id=5)]))
        if url.endswith('/users/current/'):
            return Response(dict(credit=10, api_key='TOP_SECRET'))
        return Response(dict(instances=[]))
    api = VastAPI(key='KEY', request=request)
    assert api.offer(42, 5, timeout=1)['id'] == 42
    body = calls[0][1]
    assert body['machine_id'] == {'eq': 5} and body['allocated_storage'] == 60
    assert body['type'] == 'on-demand'
    assert api.account(timeout=1) == dict(credit=10, instances=[])


def test_receipt_with_arbitrary_error_container_does_not_leak():
    assert safe_receipt(dict(error_code={'secret': 'TOP_SECRET'}))['error_code'] == 'unknown'


def test_billing_selects_only_our_instance_and_does_not_save_descriptions():
    value = dict(invoices=[dict(instance_id=7, amount=.1, description='TOP_SECRET'),
                           dict(instance_id=8, amount=100)])
    result = VastAPI(key='KEY', request=lambda *a, **k: Response(value)).billing(7, 1, timeout=1)
    assert result['amount_usd'] == .1 and result['charge_rows'] == 1
    assert result['delayed_billing_possible'] is True
    assert 'TOP_SECRET' not in json.dumps(result)


def test_measurements_v2_reproduces_historical_quota_not_64():
    data = dict(cgroup={'cpu.max': '6143999 100000', 'memory.max': '64000000000'},
                affinity=list(range(64)), meminfo='MemTotal: 251000000 kB\n',
                physical_groups=[{}] * 32, topology_known=True,
                lscpu=dict(returncode=0, stdout=json.dumps(dict(lscpu=[
                    dict(field='Model name:', data='AMD Ryzen Threadripper PRO 9975WX')]))))
    result = measurements(data)
    assert result['quota_vcpus'] == pytest.approx(61.43999)
    assert result['affinity_vcpus'] == 64 and result['physical_cores'] == 32
    assert result['usable_ram_gb'] == 64
    assert result['cpu_model'].endswith('9975WX')


def test_measurements_v1_and_unknown():
    result = measurements(dict(cgroup={'cpu/cpu.cfs_quota_us': '100000', 'cpu/cpu.cfs_period_us': '100000'}))
    assert result['quota_vcpus'] == 1
    assert result['usable_ram_gb'] is None and result['physical_cores'] is None
    assert measurements({})['quota_state'] == 'unknown'
    assert measurements(dict(cgroup={'cpu.max': 'max 100000'}))['quota_state'] == 'unlimited_visible'


def _wait(seconds):
    time.sleep(seconds)
    return True


def _pure(value):
    return value


def test_bounded_worker_success_has_no_network():
    assert bounded(_pure, ('synthetic',), 10) == 'synthetic'


def test_bounded_worker_kills_slow_operation_instead_of_leaving_thread():
    started = time.monotonic()
    with pytest.raises(TransportError, match='transport_error'):
        bounded(_wait, (30,), .2)
    assert time.monotonic() - started < 5


def test_local_prepare_and_fingerprint_no_network(tmp_path, monkeypatch):
    paramiko = pytest.importorskip('paramiko')
    from submoon_research.compute import trial_transport as t
    root = tmp_path
    (root / 'scripts').mkdir()
    (root / 'scripts/vast_hardware_probe.py').write_text('"synthetic only"\n')
    cfg = root / 'configs/compute/vast_radar.yaml'
    cfg.parent.mkdir(parents=True)
    real_cfg = Path(__file__).resolve().parents[2] / 'configs/compute/vast_radar.yaml'
    cfg.write_bytes(real_cfg.read_bytes())
    source = root / 'code.txt'
    source.write_text('synthetic')
    monkeypatch.setattr(t, 'code_paths', lambda root: [source])
    key = paramiko.RSAKey.generate(1024)
    path = root / 'synthetic_key'
    key.write_private_key_file(str(path))
    monkeypatch.setattr(t.VastAPI, 'request', lambda *a, **kw: pytest.fail('network forbidden'))
    output = root / 'prepared.json'
    result = t.prepare(root, output, path)
    assert result['paid_actions'] == 0 and result['api_schema_live_verified'] is False
    assert 'PRIVATE KEY' not in output.read_text()
    t.verify_prepared(root, output, path)
    with pytest.raises(TrialError, match='prepare_output'):
        t.prepare(root, output, path)
    source.write_text('changed')
    with pytest.raises(TrialError, match='preparation_changed'):
        t.verify_prepared(root, output, path)
