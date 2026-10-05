"""Сохранённые контрпримеры обязаны отклоняться именно реальным CLI."""
import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

from submoon_research.contracts.l1 import validate_spec

ROOT=Path(__file__).resolve().parents[2]
EVIDENCE=json.loads((ROOT/'results/evidence/W0_quality_review_v1.json').read_text(encoding='utf-8'))


def cli(spec):
    (ROOT/'scratch').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'scratch') as directory:
        p=Path(directory)/'probe.yaml'
        p.write_text(yaml.safe_dump(spec,allow_unicode=True),encoding='utf-8')
        result=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/verify_l1_specification.py'),
            '--config',str(p)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30)
    outcome=json.loads(result.stdout.strip())
    manifest=json.loads((ROOT/'runs'/outcome['run_id']/'manifest.json').read_text(encoding='utf-8'))
    return result,manifest


def canonical():
    return yaml.safe_load((ROOT/'configs/experiments/L1_baseline_spec_v0_2.yaml').read_text(encoding='utf-8'))


@pytest.mark.parametrize('probe',EVIDENCE['l1_mutations'],ids=lambda p:p['probe'])
def test_all_ten_mutations_rejected_by_cli(probe):
    spec=canonical()
    keys=probe['path'].split('.')
    node=spec
    for key in keys[:-1]:
        node=node[key]
    node[keys[-1]]=probe['injected_value']
    result,manifest=cli(spec)
    assert result.returncode!=0
    assert manifest['status']=='failed' and manifest['finished_utc']


def test_combined_invalid_and_unbound_state_rejected():
    spec=canonical()
    route=spec['routes']['independent_reproduction']
    route['frame']['rotating_frame']=True
    route['physics']['tides']=True
    route['massive_body_sets']['iapetus']=['sun']
    assert cli(spec)[0].returncode!=0
    spec=canonical()
    spec['routes']['independent_reproduction']['states']['path']=EVIDENCE['unbound_input_probe']['actual_input_path']
    result,manifest=cli(spec)
    assert result.returncode!=0 and 'SHA' in result.stdout
    assert manifest['validation']['status']!='passed'


def test_bound_inconsistent_states_and_wrong_hash_rejected():
    spec=canonical()
    probe=EVIDENCE['unbound_input_probe']
    path=probe['actual_input_path']
    spec['routes']['independent_reproduction']['states']['path']=path
    spec['inputs_sha256'][path]=probe['actual_input_sha256']
    result,_=cli(spec)
    assert result.returncode!=0 and 'error' in result.stdout
    spec['inputs_sha256'][path]='0'*64
    result,_=cli(spec)
    assert result.returncode!=0 and 'SHA' in result.stdout


def test_geometric_identity_finiteness_and_units_are_checked():
    from submoon_research.contracts.l1 import validate_states
    spec=canonical()
    states=json.loads((ROOT/spec['routes']['independent_reproduction']['states']['path']).read_text(encoding='utf-8'))
    changed=copy.deepcopy(states)
    changed['barycentric']['sun'][0][0]+=1e6
    with pytest.raises(ValueError,match='SSB'):
        validate_states(changed,spec)
    changed=copy.deepcopy(states)
    changed['barycentric']['sun'][0][0]=float('nan')
    with pytest.raises(ValueError):
        validate_states(changed,spec)
    changed=copy.deepcopy(states)
    changed['position_unit']='au'
    with pytest.raises(ValueError):
        validate_states(changed,spec)


@pytest.mark.parametrize('change',[lambda s:s.pop('gates'),lambda s:s.update(extra=True),
    lambda s:s.update(inputs_sha256={}),lambda s:s.update(production_allowed=0),
    lambda s:s['routes']['independent_reproduction']['events']['reference_contact'].update(submoon_radius_km=float('nan')),
    lambda s:s['routes']['independent_reproduction']['events']['reference_contact'].update(submoon_radius_km=float('inf'))])
def test_strict_missing_extra_empty_type_and_nonfinite(change):
    spec=copy.deepcopy(canonical())
    change(spec)
    with pytest.raises(ValueError):
        validate_spec(spec)


def test_canonical_control_and_read_dependencies_are_preserved():
    result,manifest=cli(canonical())
    assert result.returncode==0 and manifest['validation']['status']=='passed'
    states='data/processed/W0-geometric-states-v1/geometric_states.json'
    assert states in manifest['inputs_sha256']
    assert (ROOT/'runs'/manifest['run_id']/'inputs'/states).is_file()
