"""Синтетические входы конечного freeze: имитация допуска, не реальный пилот."""
import hashlib
import json

import pytest
import yaml

import submoon_research.execution as execution
from submoon_research.contracts.run_manifest import validate_run_manifest_for_admission
from submoon_research.contracts.freeze import validate_freeze
from submoon_research.contracts.w1 import W1Design


@pytest.fixture
def freeze_case(tmp_path):
    specification = W1Design(design_id='synthetic-freeze-contract', status='ready_for_freeze')
    sampling = dict(design_id=specification.design_id, horizon_seconds=specification.horizon_seconds,
                    scenario_id=specification.scenario_id, randomizations=specification.randomizations,
                    points_per_randomization=2**specification.power,
                    hosts=specification.development, measures=specification.measures)

    def put(name, data):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data).encode() if isinstance(data, dict) else data
        path.write_bytes(payload)
        return dict(path=name, sha256=hashlib.sha256(payload).hexdigest())

    code = put('pilot/code/a.py', b'# synthetic code\n')
    artifact = put('pilot/result.json', b'{"synthetic":true}')
    pilot = dict(schema_version='0.3', run_id='synthetic', status='completed',
                 validation={'status': 'passed'}, data_kind='real_nominal_development_pilot',
                 resources={'integration_calls': 1},
                 scientific_admission=dict(audited_physics=True, validated_generator=True,
                     validated_dynamics=True, validated_events=True, full_horizon_completed=True),
                 sampling_design=sampling, code_sha256={'a.py': code['sha256']},
                 artifacts_sha256={'result.json': artifact['sha256']}, inputs_sha256={},
                 initial_code_snapshot=dict(state='complete', expected_paths=['a.py'], published_count=1))
    # Все свидетельства вымышлены: изолируем проверку формата, не физику.
    inputs = []
    for name in ('cost', 'variance'):
        inputs.append(put(name+'.json', dict(run_id='synthetic', sampling_design=sampling, status='validated')))
    for name in ('numeric', 'intervals', 'predictions'):
        inputs.append(put(name+'.json', dict(status='validated', design_id=specification.design_id,
            horizon_seconds=specification.horizon_seconds, events_validated=True,
            long_dynamics_validated=True, restart_validated=True, coefficient=1,
            holdout_predictions={'synthetic': 1}, holdout_revealed=False)))
    design = dict(status='ready_for_freeze', inputs=inputs, w1_design=specification.model_dump(),
        pilot=dict(manifest='pilot/manifest.json', cost_evidence='cost.json', variance_evidence='variance.json'),
        numeric_protocol={'evidence_path': 'numeric.json'}, intervals={'evidence_path': 'intervals.json'},
        predictions={'evidence_path': 'predictions.json'},
        split=dict(development=specification.development, holdout=specification.holdout, contamination_log=[]),
        hypotheses={h: {'outcomes': ['supported', 'contradicted', 'inconclusive']} for h in ('H1', 'H2', 'H3')})
    for key in ('generator', 'domain', 'measures', 'comparisons', 'stopping', 'failures', 'horizons'):
        design[key] = {'synthetic': True}

    def publish():
        design['inputs'] = inputs + [put('pilot/manifest.json', pilot)]
        return design

    return pilot, publish, put


@pytest.mark.parametrize('version', ['0.2', '0.3'])
def test_valid_run_manifest_is_eligible_without_rewriting(tmp_path, freeze_case, version):
    pilot, publish, _ = freeze_case
    pilot['schema_version'] = version
    if version == '0.2':
        del pilot['initial_code_snapshot']
    design = publish()
    before = {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    result = validate_freeze(tmp_path, design)
    assert result['status'] == 'eligible_for_freeze'
    assert result['production_allowed'] is False
    assert result['inputs_sha256']['pilot/code/a.py'] == pilot['code_sha256']['a.py']
    assert {p: p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()} == before


def test_later_code_is_allowed_and_its_bytes_are_checked(tmp_path, freeze_case):
    pilot, publish, put = freeze_case
    late = put('pilot/code/later.py', b'# synthetic later code\n')
    pilot['code_sha256']['later.py'] = late['sha256']
    result = validate_freeze(tmp_path, publish())
    assert result['status'] == 'eligible_for_freeze'
    assert result['inputs_sha256']['pilot/code/later.py'] == late['sha256']
    assert pilot['initial_code_snapshot']['published_count'] == 1
    (tmp_path / 'pilot/code/later.py').write_bytes(b'changed')
    with pytest.raises(ValueError, match='SHA-256'):
        validate_freeze(tmp_path, publish())


@pytest.mark.parametrize('snapshot', [
    None, {}, [],
    dict(state='pending', expected_paths=['a.py'], published_count=0),
    dict(state='failed', expected_paths=['a.py'], published_count=0),
    dict(state='complete', expected_paths=['a.py'], published_count=99),
    dict(state='complete', expected_paths=['a.py'], published_count=True),
    dict(state='complete', expected_paths=['a.py'], published_count=1.0),
    dict(state='complete', expected_paths=['a.py'], published_count='1'),
    dict(state='complete', expected_paths=['a.py']),
    dict(state='complete', expected_paths=[], published_count=0),
    dict(state='complete', expected_paths='a.py', published_count=1),
    dict(state='complete', expected_paths=[None], published_count=1),
    dict(state='complete', expected_paths=[{}], published_count=1),
    dict(state='complete', expected_paths=['a.py', 'a.py'], published_count=2),
    dict(state='complete', expected_paths=['a.py', 'missing.py'], published_count=2),
])
def test_inconsistent_snapshot_rejected_by_freeze(tmp_path, freeze_case, snapshot):
    pilot, publish, _ = freeze_case
    pilot['initial_code_snapshot'] = snapshot
    with pytest.raises(ValueError, match='snapshot 0.3'):
        validate_freeze(tmp_path, publish())


def test_missing_snapshot_rejected(tmp_path, freeze_case):
    pilot, publish, _ = freeze_case
    del pilot['initial_code_snapshot']
    with pytest.raises(ValueError, match='snapshot 0.3'):
        validate_freeze(tmp_path, publish())


@pytest.mark.parametrize('path', ['', '.', './a.py', '../a.py', '/a.py', 'x//a.py',
                                  'x/../a.py', 'x/./a.py', 'x/', 'C:/a.py',
                                  'C:a.py', 'x\\a.py', 'a.py:stream', 'a\x00.py'])
def test_noncanonical_snapshot_paths_rejected(tmp_path, freeze_case, path):
    pilot, publish, _ = freeze_case
    # Не создаём опасные пути. Отказ обязан предшествовать чтению копии.
    pilot['initial_code_snapshot']['expected_paths'] = [path]
    pilot['code_sha256'][path] = pilot['code_sha256']['a.py']
    with pytest.raises(ValueError, match='snapshot 0.3'):
        validate_freeze(tmp_path, publish())


@pytest.mark.parametrize('version', [None, '0.1', '0.4', 0.3, [], {}])
def test_unknown_version_does_not_fall_back_to_legacy(tmp_path, freeze_case, version):
    pilot, publish, _ = freeze_case
    pilot['schema_version'] = version
    with pytest.raises(ValueError, match='версия RunContext'):
        validate_freeze(tmp_path, publish())


def test_missing_version_does_not_fall_back_to_legacy(tmp_path, freeze_case):
    pilot, publish, _ = freeze_case
    del pilot['schema_version']
    with pytest.raises(ValueError, match='версия RunContext'):
        validate_freeze(tmp_path, publish())


def test_legacy_with_new_marker_is_not_silently_downgraded(tmp_path, freeze_case):
    pilot, publish, _ = freeze_case
    pilot['schema_version'] = '0.2'
    with pytest.raises(ValueError, match='0.2'):
        validate_freeze(tmp_path, publish())


@pytest.mark.parametrize('index', [{}, [], None, {'a.py': 'bad'}, {'a.py': 'A'*64}, {'a.py': None},
                                   {'a.py': '0'*64, '../late.py': '0'*64}])
def test_bad_code_index_rejected(tmp_path, freeze_case, index):
    pilot, publish, _ = freeze_case
    pilot['code_sha256'] = index
    with pytest.raises(ValueError, match='snapshot 0.3'):
        validate_freeze(tmp_path, publish())


@pytest.mark.parametrize('version', ['0.2', '0.3'])
@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_missing_or_corrupt_code_rejected(tmp_path, freeze_case, version, damage):
    pilot, publish, _ = freeze_case
    pilot['schema_version'] = version
    if version == '0.2':
        del pilot['initial_code_snapshot']
    path = tmp_path / 'pilot/code/a.py'
    if damage == 'missing':
        path.unlink()
        expected_error = FileNotFoundError
    else:
        path.write_bytes(b'changed')
        expected_error = ValueError
    with pytest.raises(expected_error):
        validate_freeze(tmp_path, publish())


def test_real_runcontext_produces_compatible_snapshot(tmp_path, monkeypatch):
    package = tmp_path / 'src/submoon_research'
    package.mkdir(parents=True)
    (package / 'execution.py').write_bytes(b'# synthetic package\n')
    (package / 'contract.json').write_bytes(b'{"synthetic":true}')
    (tmp_path / 'configs').mkdir()
    (tmp_path / 'configs/resources.yaml').write_text(yaml.safe_dump(dict(
        pilot_wall_seconds=600, pilot_output_mib=256, minimum_free_disk_gib=0,
        threads_per_worker=1)), encoding='utf-8')
    monkeypatch.setattr(execution, '__file__', str(package / 'execution.py'))
    with execution.RunContext(tmp_path, 'synthetic-contract', output_mib=1) as run:
        path = run.folder / 'manifest.json'
        validate_run_manifest_for_admission(json.loads(path.read_text(encoding='utf-8')))
        later = tmp_path / 'later.py'
        later.write_bytes(b'# synthetic later code\n')
        run.save_code(later)
        manifest = json.loads(path.read_text(encoding='utf-8'))
        validate_run_manifest_for_admission(manifest)
        assert manifest['initial_code_snapshot']['published_count'] == 2
        assert len(manifest['code_sha256']) == 3
        # Структурный контракт не заявляет готовность науки.
        assert manifest['status'] == 'running'
        assert manifest['validation']['status'] == 'not_run'
    validate_run_manifest_for_admission(json.loads(path.read_text(encoding='utf-8')))


@pytest.mark.parametrize('gate', ['status', 'validation', 'physics', 'horizon', 'synthetic'])
def test_complete_snapshot_does_not_bypass_scientific_gates(tmp_path, freeze_case, gate):
    pilot, publish, _ = freeze_case
    if gate == 'status':
        pilot['status'] = 'running'
    elif gate == 'validation':
        pilot['validation']['status'] = 'not_run'
    elif gate == 'physics':
        pilot['scientific_admission']['audited_physics'] = False
    elif gate == 'horizon':
        pilot['scientific_admission']['full_horizon_completed'] = False
    else:
        pilot['data_kind'] = 'synthetic'
    with pytest.raises(ValueError):
        validate_freeze(tmp_path, publish())
