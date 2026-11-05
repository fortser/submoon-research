"""Полностью синтетические API/SSH/часы; ни одного внешнего или платного вызова."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from submoon_research.compute.hardware_trial import (
    PREFIX, Runner, TrialError, check_offer, load, safe_receipt, validate_signal,
)
from submoon_research.compute.radar import load_config


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def signal(**changes):
    value = dict(schema_version=1, approval_ref='W2-D043', user_found_station=True,
                 source_ref='synthetic-user-message-1', user_message='Синтетический сигнал для теста',
                 episode_id='synthetic-radar-event-1', offer_id=47034418, machine_id=143224,
                 task_id='W2-T056', owner='test-operator')
    value.update(changes)
    return value


def offer(**changes):
    value = dict(id=47034418, machine_id=143224, resource_type='gpu', num_gpus=1,
                 gpu_name='RTX PRO 5000', cpu_name='AMD Ryzen Threadripper PRO 9975WX 32-Cores',
                 cpu_arch='amd64', cpu_cores=64, cpu_cores_effective=64, cpu_ram=257061,
                 disk_space=1000, dph_base=.6, storage_cost=.1333333333333333,
                 dph_total=.611111111111, reliability=.995, verification='unverified',
                 direct_port_count=99, rentable=True, rented=False,
                 inet_down_cost=.0026, inet_up_cost=.0039)
    value.update(changes)
    return value


class API:
    def __init__(self, clock):
        self.clock = clock
        self.rows = []
        self.creates = 0
        self.deletes = []
        self.off = offer()
        self.credit = 10
        self.create_mode = 'normal'
        self.state = 'running'
        self.fail_delete = False
        self.fail_billing = False
        self.list_failures = 0

    def account(self, timeout):
        return dict(credit=self.credit, instances=deepcopy(self.rows))

    def offer(self, *args, **kwargs):
        return self.off

    def create(self, offer_id, **kwargs):
        self.creates += 1
        assert kwargs['image'] == 'python:3.11-slim-bookworm'
        assert kwargs['disk_gb'] == 60
        if self.create_mode == 'refuse':
            return dict(http_status=400, success=False, error_code='no_such_ask')
        if self.create_mode == 'ambiguous':
            raise RuntimeError('TOP_SECRET https://secret.invalid/?api_key=TOP_SECRET')
        self.rows.append(dict(id=123, machine_id=143224, label=kwargs['label'], actual_status=self.state))
        if self.create_mode == 'accepted_timeout':
            raise TimeoutError('TOP_SECRET')
        return dict(http_status=200, success=True, new_contract=123, instance_api_key='TOP_SECRET')

    def instances(self, timeout):
        if self.list_failures:
            self.list_failures -= 1
            raise OSError('TOP_SECRET')
        return deepcopy(self.rows)

    def instance(self, iid, timeout):
        return next(deepcopy(r) for r in self.rows if r['id'] == iid)

    def destroy(self, iid, timeout):
        self.deletes.append(iid)
        if self.fail_delete:
            raise OSError('TOP_SECRET')
        self.rows = [r for r in self.rows if r['id'] != iid]

    def billing(self, *args, **kwargs):
        if self.fail_billing:
            raise ValueError('TOP_SECRET')
        return dict(status='snapshot_not_final_invoice', amount_usd=0, delayed_billing_possible=True)


class Probe:
    def __init__(self):
        self.calls = 0
        self.error = None
        self.measured = dict(cpu_model='AMD Ryzen Threadripper PRO 9975WX 32-Cores',
                             affinity_vcpus=64, quota_vcpus=64, usable_ram_gb=257.061)
        self.checks = dict(cpu_work=True, memory_sample=True, disk_sample=True)

    def run(self, info, folder, run_id, timeout):
        self.calls += 1
        if self.error:
            raise self.error
        (folder / 'hardware.json').write_text('{"synthetic": true}')
        return dict(completed=True, checks=self.checks, measured=self.measured)


@pytest.fixture
def env(tmp_path):
    (tmp_path / 'tracking').mkdir()
    clock = Clock()
    api, probe = API(clock), Probe()
    runner = Runner(tmp_path, api, probe, clock=clock)
    return runner, api, probe, clock


def assert_safe(root):
    for path in Path(root).rglob('*.json'):
        assert 'TOP_SECRET' not in path.read_text()


def test_happy_path_one_create_download_then_delete(env):
    r, api, probe, _ = env
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert result['create_requests'] == api.creates == 1
    assert not result.get('error_code') and all(result['resource_comparison'].values())
    assert probe.calls == 1 and api.deletes == [123]
    assert api.rows == []
    assert load(r.root / 'tracking/runtime.json')['processes'] == []
    assert load(r.folder / 'sha256.json')['hardware.json']
    assert (r.folder / 'create_receipt.json').exists()
    assert_safe(r.root)


@pytest.mark.parametrize('changes', [dict(user_found_station=False), dict(approval_ref='W2-D031'),
                                     dict(source_ref=''), dict(offer_id=True), dict(machine_id=None),
                                     dict(task_id='whatever')])
def test_invalid_signal_never_contacts_api(env, changes):
    r, api, probe, _ = env
    with pytest.raises(TrialError):
        r.run(signal(**changes))
    assert api.creates == probe.calls == 0
    assert not r.base.exists()


@pytest.mark.parametrize('changes,code', [
    ({'rentable': False}, 'offer_unavailable'),
    ({'rented': True}, 'offer_unavailable'),
    ({'resource_type': 'disk'}, 'offer_disk_contract'),
    ({'verification': 'deverified'}, 'offer_verification'),
    ({'verification': None}, 'offer_verification'),
    ({'dph_base': .7}, 'offer_target_price'),
    ({'storage_cost': None}, 'offer_unknown_storage_price'),
    ({'cpu_cores_effective': 32}, 'offer_vcpu'),
    ({'cpu_ram': 1000}, 'offer_ram'),
    ({'inet_up_cost': None}, 'unknown_traffic_price'),
    ({'inet_down_cost': float('nan')}, 'unknown_traffic_price'),
    ({'inet_up_cost': 100}, 'traffic_price_cap'),
    ({'dph_total': 1}, 'inconsistent_total_price'),
    ({'dph_total': None}, 'inconsistent_total_price'),
    ({'id': 9}, 'offer_identity'),
])
def test_precreate_gates(env, changes, code):
    r, api, probe, _ = env
    api.off.update(changes)
    result = r.run(signal())
    assert result['status'] == 'rejected' and result['error_code'] == code
    assert api.creates == probe.calls == 0 and api.deletes == []


def test_excluded_machine_cannot_return_via_signal(env):
    r, api, _, _ = env
    api.off['machine_id'] = 139652
    assert r.run(signal(machine_id=139652))['error_code'] == 'offer_excluded_machine'
    assert api.creates == 0


@pytest.mark.parametrize('credit', [None, True, float('nan'), .99])
def test_unknown_or_insufficient_credit(env, credit):
    r, api, _, _ = env
    api.credit = credit
    assert r.run(signal())['status'] == 'rejected'
    assert api.creates == 0


@pytest.mark.parametrize('mutation', [dict(), dict(source_ref='another-message', offer_id=99),
                                     dict(episode_id='another-event')])
def test_duplicate_source_or_episode_never_creates_again(env, mutation):
    r, api, _, _ = env
    r.run(signal())
    with pytest.raises(TrialError, match='duplicate_signal_or_episode'):
        Runner(r.root, api, Probe(), clock=r.clock).run(signal(**mutation))
    assert api.creates == 1


def test_timeout_after_accepted_create_reconciles_and_tests_once(env):
    r, api, probe, _ = env
    api.create_mode = 'accepted_timeout'
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert result['create_receipt']['error_code'] == 'transport_error'
    assert api.creates == probe.calls == 1 and api.deletes == [123]
    assert_safe(r.root)


def test_ambiguous_create_no_retry_and_runtime_remains(env):
    r, api, probe, _ = env
    api.create_mode = 'ambiguous'
    result = r.run(signal())
    assert result['status'] == 'needs_attention' and result['instance_id'] is None
    assert api.creates == 1 and probe.calls == 0
    runtime = load(r.root / 'tracking/runtime.json')['processes']
    assert len(runtime) == 1 and runtime[0]['instance_id'] is None
    with pytest.raises(TrialError, match='unresolved_trial'):
        Runner(r.root, api, probe, clock=r.clock).run(signal(source_ref='new', episode_id='new'))
    assert api.creates == 1
    assert_safe(r.root)


def test_explicit_create_refusal_no_probe_no_delete(env):
    r, api, probe, _ = env
    api.create_mode = 'refuse'
    result = r.run(signal())
    assert result['status'] == 'create_refused'
    assert probe.calls == 0 and api.creates == 1 and not api.deletes
    assert load(r.root / 'tracking/runtime.json')['processes'] == []


@pytest.mark.parametrize('state', ['exited', 'offline', 'unknown', 'stopped', 'loading'])
def test_provisioning_failures_or_deadline_cleanup(env, state):
    r, api, probe, clock = env
    api.state = state
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert result['error_code'] in {'provisioning_failed', 'provisioning_deadline'}
    assert probe.calls == 0 and api.deletes == [123]
    assert clock.now < result['deadline_epoch']


@pytest.mark.parametrize('error', [OSError('TOP_SECRET'), TimeoutError('TOP_SECRET'),
                                  TrialError('download_sha'), TrialError('download_limit'),
                                  TrialError('probe_deadline'), TrialError('remote_probe_failed')])
def test_probe_errors_preserve_error_and_cleanup(env, error):
    r, api, probe, _ = env
    probe.error = error
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified' and result['error_code']
    assert api.deletes == [123] and api.creates == 1
    assert_safe(r.root)


def test_destroy_failure_stays_visible_and_recover_never_creates(env):
    r, api, probe, clock = env
    api.fail_delete = True
    result = r.run(signal())
    assert result['status'] == 'needs_attention'
    assert load(r.root / 'tracking/runtime.json')['processes'][0]['instance_id'] == 123
    api.fail_delete = False
    again = Runner(r.root, api, probe, clock=clock).recover(result['run_id'])
    assert again['status'] == 'destroyed_verified'
    assert api.creates == probe.calls == 1
    assert_safe(r.root)


def test_late_contract_after_ambiguous_create_recovery_only_deletes(env):
    r, api, probe, clock = env
    api.create_mode = 'ambiguous'
    result = r.run(signal())
    clock.sleep(4000)
    api.rows = [dict(id=456, machine_id=143224, label=result['label'], actual_status='running')]
    again = Runner(r.root, api, probe, clock=clock).recover(result['run_id'])
    assert again['status'] == 'destroyed_verified'
    assert api.creates == 1 and probe.calls == 0 and api.deletes == [456]


def test_unrelated_instances_and_runtime_preserved(env):
    r, api, _, _ = env
    foreign = dict(id=9, machine_id=42, label='personal', actual_status='running')
    api.rows.append(foreign)
    (r.root / 'tracking/runtime.json').write_text(json.dumps(dict(processes=[dict(run_id='radar', pid=1)])))
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert api.rows == [foreign] and api.deletes == [123]
    assert load(r.root / 'tracking/runtime.json')['processes'] == [dict(run_id='radar', pid=1)]


def test_untracked_hardware_contract_blocks_new_rental(env):
    r, api, _, _ = env
    api.rows = [dict(id=9, machine_id=143224, label=PREFIX+'other', actual_status='running')]
    assert r.run(signal())['error_code'] == 'existing_hardware_contract'
    assert api.creates == 0 and not api.deletes


def test_billing_failure_is_not_resource_leak(env):
    r, api, _, _ = env
    api.fail_billing = True
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert load(r.folder / 'billing.json')['status'] == 'unverified'


def test_receipt_removes_secrets():
    r = safe_receipt(dict(http_status=400, error_code='TOP_SECRET', instance_api_key='TOP_SECRET'))
    assert 'TOP_SECRET' not in json.dumps(r)


def test_policy_uses_same_assess_and_fixed_hourly_cap():
    terms = check_offer(offer(), signal(), load_config())
    assert terms['budget_usd'] == 1 and terms['max_seconds'] == 1800
    assert terms['hourly_usd'] < .65


@pytest.mark.parametrize('changes', [dict(quota_vcpus=61.44), dict(affinity_vcpus=32),
                                     dict(usable_ram_gb=64), dict(cpu_model='QEMU Virtual CPU')])
def test_measured_resource_mismatch_is_not_hardware_pass(env, changes):
    r, api, probe, _ = env
    probe.measured.update(changes)
    result = r.run(signal())
    assert result['error_code'] == 'advertised_resources_mismatch_or_unknown'
    assert False in result['resource_comparison'].values()
    assert result['status'] == 'destroyed_verified' and api.deletes == [123]


def test_unknown_measurements_remain_null(env):
    r, _, probe, _ = env
    probe.measured = {}
    result = r.run(signal())
    assert all(v is None for v in result['resource_comparison'].values())
    assert result['error_code'] == 'advertised_resources_mismatch_or_unknown'


def test_memory_sample_failure_saved_and_cleanup_still_runs(env):
    r, api, probe, _ = env
    probe.checks['memory_sample'] = False
    result = r.run(signal())
    assert result['error_code'] == 'probe_checks_failed'
    assert result['probe']['checks']['memory_sample'] is False
    assert api.deletes == [123]


def test_signal_extra_field_cannot_grant_more_budget():
    with pytest.raises(TrialError):
        validate_signal(signal(budget_usd=999))
