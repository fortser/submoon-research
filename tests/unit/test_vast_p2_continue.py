"""Offline safety checks for the explicitly approved existing-contract adapter."""
import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/vast_p2_continue.py'
    spec = importlib.util.spec_from_file_location('continue_test', path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def plan():
    return dict(approved=True, approval_ref='W2-D033', instance_id=56220810, scenario='p2_2a',
                budget_usd=13, max_hours=18, run_deadline_hours=16.5, max_hourly_usd=.611112,
                physical_cpu_ids=list(range(32)))


def rental():
    return dict(instance_id=56220810, scenario='p2_2a', offer=dict(dph_total=.611111,
                cpu_cores_effective=64, cpu_ram=257061), min_workers=32,
                run_deadline_utc='2026-10-17T00:39:40Z')


def test_plan_bounds_and_existing_contract():
    obj = module()
    obj.check_plan(plan(), rental())
    for key, value in [('instance_id', 55), ('approved', False), ('budget_usd', 14),
                       ('max_hours', 19), ('run_deadline_hours', 18),
                       ('physical_cpu_ids', list(range(31)))]:
        with pytest.raises(ValueError):
            obj.check_plan(dict(plan(), **{key: value}), rental())


def test_transport_does_not_create_or_touch_other_contract(monkeypatch):
    obj = module()
    monkeypatch.setattr(obj.vp, 'load', lambda _: rental())
    for args in [('create', 'instance', 47034418), ('destroy', 'instance', 55)]:
        with pytest.raises(ValueError):
            obj.provider_cli(*args)


def test_script_preserves_gates_and_pins_physical_cores(monkeypatch):
    obj = module()
    monkeypatch.setattr(obj.vp, 'approval', plan)
    code = obj.start_script(rental())
    assert code.index('SOURCE SHA MISMATCH') < code.index('apt-get update')
    assert code.index('|| fail pytest') < code.index('taskset -pc 0,1,2')
    assert code.index('taskset -pc 0,1,2') < code.index('echo "baseline')
    assert '--margin 1.15' in code
    assert '|| fail baseline\n' in code
    assert '--per-cell 128 --b-per-cell 16' in code
    assert '--expect-full --expect-decision-ready' in code
    assert '--deadline-utc 2026-10-17T00:39:40Z' in code
