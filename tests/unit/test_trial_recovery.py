"""Отрицательная приёмка: сбои реестра/диска, дубликаты и контроль времени."""
import importlib.util
from pathlib import Path

import pytest

from submoon_research.compute import hardware_trial as h
from submoon_research.compute.radar import single_instance

# Общие фейки, никакого API/SSH или научной нагрузки.
from test_hardware_trial import API, Clock, Probe, signal  # noqa: E402


@pytest.fixture
def env(tmp_path):
    (tmp_path / 'tracking').mkdir()
    clock = Clock()
    api, probe = API(clock), Probe()
    runner = h.Runner(tmp_path, api, probe, clock=clock)
    return runner, api, probe, clock


def test_os_lock_prevents_concurrent_execution(env):
    r, api, _, _ = env
    with single_instance(r.base):
        with pytest.raises(Exception):
            r.run(signal())
    assert api.creates == 0


def test_foreign_label_with_our_id_is_never_destroyed(env):
    r, api, _, _ = env
    original = api.create
    def wrong_identity(*args, **kwargs):
        result = original(*args, **kwargs)
        api.rows[0]['label'] = 'personal-instance'
        return result
    api.create = wrong_identity
    result = r.run(signal())
    assert result['status'] == 'needs_attention' and api.creates == 1
    assert api.deletes == [] and api.rows


def test_foreign_machine_with_matching_label_is_not_destroyed(env):
    r, api, _, _ = env
    original = api.create
    def wrong_machine(*args, **kwargs):
        result = original(*args, **kwargs)
        api.rows[0]['machine_id'] = 999
        return result
    api.create = wrong_machine
    result = r.run(signal())
    assert result['status'] == 'needs_attention' and api.deletes == []


def test_disk_failure_after_create_cannot_prevent_delete(env, monkeypatch):
    r, api, _, _ = env
    original_create, original_save = api.create, h.save
    active = {'failed': False}
    def create(*args, **kwargs):
        result = original_create(*args, **kwargs)
        active['failed'] = True
        return result
    def save(path, value):
        if active['failed']:
            raise OSError('synthetic full disk')
        return original_save(path, value)
    api.create = create
    monkeypatch.setattr(h, 'save', save)
    # Отказ записи create_receipt случается до record(instance_id); ID обязан
    # оставаться известным процессу для cleanup, даже если диск уже отказал.
    try:
        r.run(signal())
    except OSError:
        pass
    assert api.creates == 1
    assert api.deletes == [123]


def test_disk_failure_after_accepted_timeout_still_reconciles_and_deletes(env, monkeypatch):
    r, api, _, _ = env
    api.create_mode = 'accepted_timeout'
    original_create, original_save = api.create, h.save
    broken = {'yes': False}
    def create(*args, **kwargs):
        try:
            return original_create(*args, **kwargs)
        finally:
            broken['yes'] = True
    def save(path, value):
        if broken['yes']:
            raise OSError('synthetic disk failure')
        return original_save(path, value)
    api.create = create
    monkeypatch.setattr(h, 'save', save)
    try:
        r.run(signal())
    except OSError:
        pass
    assert api.creates == 1 and api.deletes == [123]


def test_runtime_failure_after_create_does_not_prevent_cleanup(env, monkeypatch):
    r, api, _, _ = env
    original = h.runtime
    def registry(root, manifest, folder, active):
        if manifest.get('instance_id'):
            raise OSError('synthetic registry failure')
        return original(root, manifest, folder, active)
    monkeypatch.setattr(h, 'runtime', registry)
    result = r.run(signal())
    assert api.deletes == [123] and not api.rows
    assert result['runtime_update_failed'] is True


def test_recover_precreate_crash_has_no_network_or_create(env):
    r, api, probe, _ = env
    r._claim(signal())  # emulate crash during local preflight, before the token
    again = h.Runner(r.root, api, probe, clock=r.clock).recover(r.manifest['run_id'])
    assert again['status'] == 'rejected'
    assert api.creates == 0 and api.deletes == [] and probe.calls == 0


def test_eventual_visibility_after_more_than_six_polls(env):
    r, api, probe, _ = env
    api.create_mode = 'accepted_timeout'
    original = api.instances
    calls = {'n': 0}
    def instances(timeout):
        calls['n'] += 1
        return [] if calls['n'] < 9 else original(timeout)
    api.instances = instances
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified'
    assert api.creates == probe.calls == 1 and api.deletes == [123]


def test_empty_inventory_at_cleanup_uses_targeted_get_then_delete(env):
    r, api, _, _ = env
    original = api.instances
    calls = {'n': 0}
    def instances(timeout):
        calls['n'] += 1
        return [] if calls['n'] == 1 else original(timeout)
    api.instances = instances
    result = r.run(signal())
    assert result['status'] == 'destroyed_verified' and api.deletes == [123]


def test_stale_offer_rejected_even_if_all_gates_match(env, monkeypatch):
    r, api, _, clock = env
    # Детерминированный scheduler: offer завершён до долгого account.result().
    class Future:
        def __init__(self, fn, args, kwargs):
            self.fn, self.args, self.kwargs = fn, args, kwargs
        def result(self):
            clock.sleep(31)
            return self.fn(*self.args, **self.kwargs)
    class Pool:
        def __init__(self, **kwargs):
            self.n = 0
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def submit(self, fn, *args, **kwargs):
            self.n += 1
            if self.n == 1:
                return Future(fn, args, kwargs)
            value = fn(*args, **kwargs)
            class Ready:
                def result(self):
                    return value
            return Ready()
    monkeypatch.setattr(h, 'ThreadPoolExecutor', Pool)
    assert r.run(signal())['error_code'] == 'stale_offer'
    assert api.creates == 0


def test_probe_deadline_cannot_be_reported_as_success(env):
    r, api, probe, clock = env
    original = probe.run
    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        clock.sleep(1700)
        return result
    probe.run = slow
    result = r.run(signal())
    assert result['error_code'] == 'probe_deadline' and api.deletes == [123]


def test_terminal_recovery_clears_stale_runtime_without_second_create(env):
    r, api, probe, clock = env
    result = r.run(signal())
    h.save(r.root / 'tracking/runtime.json', dict(processes=[dict(run_id=result['run_id'])]))
    again = h.Runner(r.root, api, probe, clock=clock).recover(result['run_id'])
    assert again['status'] == 'destroyed_verified'
    assert h.load(r.root / 'tracking/runtime.json')['processes'] == []
    assert api.creates == 1 and api.deletes == [123]


def test_capacity_gate_pure_no_cpu_load():
    path = Path(__file__).resolve().parents[2] / 'scripts/vast_hardware_probe.py'
    spec = importlib.util.spec_from_file_location('hardware_probe_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.capacity_check(dict(equivalent_busy_cpus=31, workers=32),
                                 dict(equivalent_busy_cpus=61, workers=64))
    assert not module.capacity_check(None, dict(equivalent_busy_cpus=64, workers=64))
    assert not module.capacity_check(dict(equivalent_busy_cpus=10, workers=32),
                                     dict(equivalent_busy_cpus=64, workers=64))


def test_cli_run_requires_new_signal_and_active_task_before_api(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/vast_hardware_trial.py'
    spec = importlib.util.spec_from_file_location('hardware_cli_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    (tmp_path / 'tracking/stages/W2').mkdir(parents=True)
    sig = tmp_path / 'signal.json'
    h.save(sig, signal())
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, 'VastAPI', lambda: pytest.fail('network forbidden'))
    assert module.main(['run', '--signal', str(sig), '--prepared', str(tmp_path / 'absent')]) == 2
