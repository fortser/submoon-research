"""Рабочий контракт не должен разрешить незаявленную физику или готовый пилот."""
import json
from copy import deepcopy
from pathlib import Path

import yaml

from submoon_research.workflows.l1_specification import verify_l1_contract

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    spec = yaml.safe_load((ROOT / 'configs/experiments/L1_baseline_spec_v0_2.yaml').read_text(encoding='utf-8'))
    route = spec['routes']['independent_reproduction']
    states = json.loads((ROOT / route['states']['path']).read_text(encoding='utf-8'))
    passports = {host: yaml.safe_load((ROOT / entry['passport']).read_text(encoding='utf-8'))
                 for host, entry in route['hosts'].items()}
    return [spec, states, passports,
        (ROOT / 'data/raw/W0-himalia-sources-20261004/jup344.cmt').read_text(encoding='utf-8'),
        (ROOT / 'data/raw/W0-l1-sources-20261004/jup347.cmt').read_text(encoding='utf-8')]


def test_operational_events_cannot_be_promoted_to_physical_escape():
    args = inputs()
    args[0]['routes']['independent_reproduction']['events']['article_energy_crossing']['physical_permanent_escape'] = True
    checks, _ = verify_l1_contract(*args)
    assert not checks['energy_is_operational_only']


def test_massive_submoon_requires_different_equations_and_new_contract():
    args = inputs()
    args[0] = deepcopy(args[0])
    args[0]['routes']['independent_reproduction']['physics']['submoon_backreaction'] = True
    checks, _ = verify_l1_contract(*args)
    assert not checks['massless_limit_labeled']


def test_forcing_and_production_are_rejected():
    args = inputs()
    args[0]['integration_allowed'] = True
    args[0]['routes']['independent_reproduction']['states']['ephemeris_forcing'] = True
    checks, _ = verify_l1_contract(*args)
    assert not checks['no_runtime_authorized']
    assert not checks['initial_conditions_not_forcing']
