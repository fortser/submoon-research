"""Синтетический негативный контроль freeze, не научный запуск."""
import hashlib
import json
import tempfile
from pathlib import Path

from submoon_research.contracts.freeze import validate_freeze
from submoon_research.contracts.w1 import W1Design


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(root, version, snapshot, listed=True, corrupt=False):
    spec = W1Design(design_id='synthetic-test', status='ready_for_freeze')
    sampling = dict(design_id=spec.design_id, horizon_seconds=spec.horizon_seconds,
                    scenario_id=spec.scenario_id, randomizations=spec.randomizations,
                    points_per_randomization=2**spec.power, hosts=spec.development, measures=spec.measures)
    def put(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(value).encode() if isinstance(value, dict) else value
        path.write_bytes(data)
        return dict(path=name, sha256=digest(data))
    code = b'synthetic code\n'
    put('pilot/code/a.py', code)
    put('pilot/result.json', b'{}')
    pilot = dict(schema_version=version, run_id='synthetic', status='completed', validation={'status':'passed'},
        data_kind='real_nominal_development_pilot', resources={'integration_calls':1},
        scientific_admission=dict(audited_physics=True, validated_generator=True, validated_dynamics=True,
                                  validated_events=True, full_horizon_completed=True),
        sampling_design=sampling, code_sha256={'a.py': digest(b'wrong' if corrupt else code)} if listed else {},
        artifacts_sha256={'result.json':digest(b'{}')}, inputs_sha256={})
    if snapshot is not None:
        pilot['initial_code_snapshot'] = snapshot
    inputs = [put('pilot/manifest.json',pilot)]
    for name in ['cost','variance']:
        inputs.append(put(name+'.json',dict(run_id='synthetic', sampling_design=sampling,status='validated')))
    for name in ['numeric','intervals','predictions']:
        evidence = dict(status='validated',design_id=spec.design_id, horizon_seconds=spec.horizon_seconds,
                        events_validated=True,long_dynamics_validated=True,restart_validated=True,
                        coefficient=1,holdout_predictions={'control':1},holdout_revealed=False)
        inputs.append(put(name+'.json',evidence))
    design = dict(status='ready_for_freeze', inputs=inputs, w1_design=spec.model_dump(),
        pilot=dict(manifest='pilot/manifest.json',cost_evidence='cost.json',variance_evidence='variance.json'),
        numeric_protocol={'evidence_path':'numeric.json'}, intervals={'evidence_path':'intervals.json'},
        predictions={'evidence_path':'predictions.json'},
        split=dict(development=spec.development,holdout=spec.holdout,contamination_log=[]),
        hypotheses={h:{'outcomes':['supported','contradicted','inconclusive']} for h in ('H1','H2','H3')})
    for key in ('generator','domain','measures','comparisons','stopping','failures','horizons'):
        design[key] = {'synthetic':True}
    try:
        return validate_freeze(root, design)['status']
    except Exception as exc:
        return type(exc).__name__ + ': ' + str(exc)


cases = {
    'legacy_0.2': ('0.2', None, True, False),
    'complete_0.3': ('0.3', dict(state='complete',expected_paths=['a.py'],published_count=1), True, False),
    'pending_with_nonempty_index': ('0.3', dict(state='pending',expected_paths=['a.py','missing.py'],published_count=0), True, False),
    'complete_missing_expected': ('0.3', dict(state='complete',expected_paths=['a.py','missing.py'],published_count=2), True, False),
    'complete_wrong_count': ('0.3', dict(state='complete',expected_paths=['a.py'],published_count=99), True, False),
    'empty_index': ('0.3', dict(state='pending',expected_paths=['a.py'],published_count=0), False, False),
    'wrong_digest': ('0.3', dict(state='complete',expected_paths=['a.py'],published_count=1), True, True),
}
for name, args in cases.items():
    with tempfile.TemporaryDirectory(prefix='w2_t073_synthetic_') as temp:
        print(name, '=>', run(Path(temp), *args))
