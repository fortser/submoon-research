"""OPT-03/04: файловые защиты без REBOUNDx и без научных интеграций.

Synthetic worker используется только для проверки I/O, не численного допуска.
"""
import copy
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from submoon_research.contracts.p2_files import (
    atomic_json, contract_digest, exclusive_run, output_path, preflight, read_job_rows, read_json,
    source_manifest, units_digest, validate_units_document,
)

ROOT = Path(__file__).resolve().parents[2]
ARGS = ['--per-cell', '1', '--b-per-cell', '0', '--years', '0.0003', '--b-years', '--workers', '0']


def load(name):
    sys.path.insert(0, str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stand(monkeypatch):
    mod = load('admission_p2_2')
    monkeypatch.setattr(mod, 'b_fingerprint', lambda: 'synthetic-B')
    monkeypatch.setattr(mod, 'implementation_fingerprint', lambda: 'synthetic-D')
    monkeypatch.setattr(mod, '_init', lambda: None)

    def worker(job):
        u = job['unit']
        partial = job.get('stop_file') and Path(job['stop_file']).exists()
        end = 0.0 if partial else job['years']*mod.YEAR
        return dict(orbit_id=u['orbit_id'], host=u['host'], measure=u['measure'], unit=u['unit'],
                    seed=u['seed'], years=job['years'], variant=job['variant'],
                    stand_version=mod.STAND_VERSION, prototype_version=mod.PROTOTYPE_VERSION,
                    job_fingerprint=job['fingerprint'], wall=0.0, cpu=0.0,
                    run_status='partial' if partial else 'completed',
                    error='stopped_by_deadline' if partial else None,
                    outcome='unresolved' if partial else 'survived',
                    unresolved_reason='computational' if partial else None,
                    last_valid_time=end, event=None, steps=0, nfev=0,
                    eccentricity=None, numerical_domain=None, endpoint_residual=None,
                    stopped=bool(partial), final_probe=u['state'][-6:],
                    samples=[(0.0, u['state'][-6:]), (end, u['state'][-6:])])

    monkeypatch.setattr(mod, '_job', worker)
    return mod


@pytest.fixture
def document(stand):
    params = stand.normalize_params(1, 0, 0, [0.0003], [])
    return stand.units_document(stand.units(1), params, {'compiled': 'synthetic-D', 'B': 'synthetic-B'})


MUTATIONS = [
    ('state', lambda d: d['units'][0]['state'].__setitem__(-1, 10.0)),
    ('gm', lambda d: d['units'][0]['gms'].__setitem__(0, 99.0)),
    ('radius', lambda d: d['units'][0]['radii'].__setitem__(0, 99.0)),
    ('window', lambda d: d['units'][0].update(escape_window=1.0)),
    ('period', lambda d: d['units'][0].update(period_seconds=1.0)),
    ('figure', lambda d: d['units'][0]['figures'][0].update(j2=0.1)),
    ('dimension', lambda d: d['units'][0]['state'].pop()),
    ('missing', lambda d: d['units'].pop()),
    ('duplicate', lambda d: d['units'].append(copy.deepcopy(d['units'][0]))),
    ('seed', lambda d: d['units'][1].update(seed=d['units'][0]['seed'])),
    ('sha', lambda d: d.update(units_sha256='0'*64)),
    ('full_sha', lambda d: d.update(contract_sha256='0'*64)),
    ('params', lambda d: d['params'].update(per_cell=2)),
    ('numerics', lambda d: d.update(generator_numerics='other')),
    ('mode', lambda d: d['contract']['modes']['D'].update(rtol=0.1)),
    ('nan', lambda d: d['units'][0]['state'].__setitem__(0, float('nan'))),
    ('inf', lambda d: d['units'][0]['gms'].__setitem__(0, float('inf'))),
    ('type', lambda d: d['units'][0].update(seed=True)),
    ('units', lambda d: d['contract'].update(time_unit='year')),
]


@pytest.mark.parametrize('name,change', MUTATIONS, ids=[x[0] for x in MUTATIONS])
def test_units_tampering_rejected_without_writes(stand, document, tmp_path, name, change):
    run = tmp_path/'run'
    run.mkdir()
    doc = copy.deepcopy(document)
    change(doc)
    (run/'units.json').write_text(json.dumps(doc), encoding='utf-8')
    before = source_manifest(run)
    assert stand.main([*ARGS, '--run-dir', str(run)]) == 2
    assert source_manifest(run) == before
    with pytest.raises(ValueError):
        validate_units_document(doc, document)


@pytest.mark.parametrize('name,change', MUTATIONS[:6], ids=[x[0] for x in MUTATIONS[:6]])
def test_rehashed_tampering_and_legacy_rejected(document, name, change):
    bad = copy.deepcopy(document)
    change(bad)
    bad['units_sha256'] = units_digest(bad['units'])
    bad['contract_sha256'] = contract_digest(bad)
    with pytest.raises(ValueError, match='ожидаемым'):
        validate_units_document(bad, document)
    bad.pop('contract')
    bad.pop('contract_sha256')
    with pytest.raises(ValueError):
        validate_units_document(bad, document)


def test_valid_units_and_bad_json(document, stand, tmp_path):
    assert validate_units_document(document, document) == document
    legacy = {k: v for k, v in document.items() if k not in ('contract', 'contract_sha256')}
    assert validate_units_document(legacy, document) == legacy
    run = tmp_path/'broken'
    run.mkdir()
    for text in ('{', '[]', '{"units":[],"units":[]}'):
        (run/'units.json').write_text(text, encoding='utf-8')
        before = source_manifest(run)
        assert stand.main([*ARGS, '--run-dir', str(run)]) == 2
        assert source_manifest(run) == before


def test_completed_source_read_only_and_analysis_exclusive(stand, tmp_path):
    run = tmp_path/'run'
    assert stand.main([*ARGS, '--run-dir', str(run)]) == 0
    before = source_manifest(run)
    for extra in ([], ['--resume'], ['--evaluate-only'], ['--analysis-dir', str(tmp_path/'out')],
                  ['--evaluate-only', '--analysis-dir', str(run/'..'/'run')],
                  ['--evaluate-only', '--analysis-dir', str(run/'nested')],
                  ['--evaluate-only', '--analysis-dir', str(tmp_path)]):
        assert stand.main([*ARGS, '--run-dir', str(run), *extra]) == 2
        assert source_manifest(run) == before
    out = tmp_path/'analysis'
    args = [*ARGS, '--run-dir', str(run), '--evaluate-only', '--analysis-dir', str(out)]
    assert stand.main(args) == 0
    assert read_json(out/'summary.json')['source'] == before
    analysis_before = source_manifest(out)
    assert stand.main(args) == 2
    assert source_manifest(out) == analysis_before and source_manifest(run) == before
    # Удаление seal/summary/progress/acceptance не снимает защиту полного состава.
    for name in ('sealed.json', 'summary.json', 'progress.json'):
        (run/name).unlink()
    before = source_manifest(run)
    assert stand.main([*ARGS, '--run-dir', str(run), '--resume']) == 2
    assert source_manifest(run) == before


def test_resume_only_partial_matching_contract(stand, tmp_path):
    run, stop = tmp_path/'run', tmp_path/'STOP'
    stop.touch()
    args = [*ARGS, '--run-dir', str(run), '--stop-file', str(stop)]
    assert stand.main(args) == 1
    before = source_manifest(run)
    assert stand.main(args) == 2
    assert stand.main([*args, '--resume', '--unit-start', '1']) == 2
    assert source_manifest(run) == before
    stop.unlink()
    assert stand.main([*args, '--resume']) == 0
    assert len((run/'jobs.jsonl').read_text().splitlines()) == 36


@pytest.mark.parametrize('corruption', ['tail', 'middle', 'blank', 'foreign', 'duplicate', 'schema'])
def test_jsonl_rejects_damage(tmp_path, corruption):
    path = tmp_path/'jobs.jsonl'
    row = dict(orbit_id='x', years=1.0, variant='D', job_fingerprint='f', run_status='completed')
    line = json.dumps(row)+'\n'
    text = {'tail': line+'{', 'middle': '{\n'+line, 'blank': '\n'+line,
            'foreign': json.dumps(dict(row, job_fingerprint='bad'))+'\n',
            'duplicate': line+line, 'schema': '[]\n'}[corruption]
    path.write_text(text, encoding='utf-8')
    with pytest.raises(ValueError):
        read_job_rows(path, {('x', 1.0, 'D'): 'f'})
    assert path.read_text(encoding='utf-8') == text


def test_aliases_and_atomic_writes(tmp_path, monkeypatch):
    run = tmp_path/'run'
    run.mkdir()
    (run/'jobs.jsonl').touch()
    alias = tmp_path/'alias'
    try:
        alias.symlink_to(run, target_is_directory=True)
    except OSError:
        # Windows без привилегии symlink: junction даёт тот же реальный alias.
        if os.name != 'nt':
            raise
        import subprocess
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(alias), str(run)], check=True, capture_output=True)
    with pytest.raises(ValueError):
        preflight(run, alias, evaluate=True, resume=False, rows={}, expected_fp={}, contract={})
    with pytest.raises(ValueError):
        output_path(alias/'acceptance.json', run)
    path = tmp_path/'output.json'
    atomic_json(path, {'v': 1})
    before = path.read_bytes()
    with pytest.raises(FileExistsError):
        atomic_json(path, {'v': 2})
    assert path.read_bytes() == before
    def fail(*args):
        raise OSError('injected publication failure')
    monkeypatch.setattr(os, 'replace', fail)
    with pytest.raises(OSError):
        atomic_json(path, {'v': 3}, replace=True)
    assert path.read_bytes() == before and not list(tmp_path.glob('.*.tmp'))


@pytest.mark.parametrize('change', ['state', 'gm', 'params', 'missing_units', 'manifest'])
def test_analysis_and_acceptor_reject_tampered_inputs(stand, tmp_path, change):
    run = tmp_path/'run'
    assert stand.main([*ARGS, '--run-dir', str(run)]) == 0
    verify = load('verify_p2_2_run')
    verify_args = ['--run', str(run), '--per-cell', '1', '--b-per-cell', '0',
                   '--years', '0.0003', '--b-years']
    assert verify.verify(verify.parse(verify_args))['passed']
    if change in ('state', 'gm'):
        doc = read_json(run/'units.json')
        field = 'state' if change == 'state' else 'gms'
        doc['units'][0][field][0] += 1.0
        doc['units_sha256'] = units_digest(doc['units'])
        doc['contract_sha256'] = contract_digest(doc)
        (run/'units.json').write_text(json.dumps(doc), encoding='utf-8')
    elif change == 'missing_units':
        (run/'units.json').unlink()
    elif change == 'manifest':
        (run/'run_contract.json').write_text('{}', encoding='utf-8')
    else:
        doc = read_json(run/'summary.json')
        doc['params']['unit_start'] = 2
        (run/'summary.json').write_text(json.dumps(doc), encoding='utf-8')
    before = source_manifest(run)
    out = tmp_path/'analysis'
    assert stand.main([*ARGS, '--run-dir', str(run), '--evaluate-only', '--analysis-dir', str(out)]) == 2
    assert not out.exists() and source_manifest(run) == before
    if change != 'manifest':  # приёмщик проверяет входы и jobs, не управляющий manifest
        assert not verify.verify(verify.parse(verify_args))['passed']


@pytest.mark.parametrize('name', ['verify_p2_run', 'verify_p2_2_run'])
def test_acceptor_cannot_overwrite_source_or_report(tmp_path, name):
    mod = load(name)
    run = tmp_path/'run'
    run.mkdir()
    sentinel = run/'acceptance.json'
    sentinel.write_text('unchanged', encoding='utf-8')
    args = ['--run', str(run)]
    if name == 'verify_p2_run':
        args += ['--years', '1', '--b-max-years', '1']
    assert mod.main([*args, '--output', str(sentinel)]) == 2
    assert sentinel.read_text(encoding='utf-8') == 'unchanged'
    out = tmp_path/'report.json'
    out.write_text('prior', encoding='utf-8')
    assert mod.main([*args, '--output', str(out)]) == 2
    assert out.read_text(encoding='utf-8') == 'prior'


def test_run_lock_prevents_second_writer(stand, tmp_path):
    run = tmp_path/'run'
    with exclusive_run(run, ROOT/'scratch/p2_locks'):
        assert stand.main([*ARGS, '--run-dir', str(run)]) == 2
        assert not run.exists()
    assert stand.main([*ARGS, '--run-dir', str(run)]) == 0


def test_p21_entrypoint_protects_rows_and_analysis(monkeypatch, tmp_path):
    mod = load('admission_p2_horizons')
    case = dict(orbit_id='synthetic', period_seconds=1.0, a_km=1.0)
    monkeypatch.setattr(mod, 'load_cases', lambda root: (None, [case]))
    monkeypatch.setattr(mod, 'b_fingerprint', lambda: 'B')
    monkeypatch.setattr(mod, 'implementation_fingerprint', lambda: 'D')
    monkeypatch.setattr(mod, 'job_fingerprint', lambda *args: 'f')
    run = tmp_path/'p21'
    run.mkdir()
    rows = [dict(orbit_id='synthetic', years=1.0, variant=v, job_fingerprint='f',
                 run_status='completed', error=None, outcome='survived', last_valid_time=mod.YEAR,
                 event=None, samples=[(0.0, [1.0, 0, 0, 0, 1.0, 0])], wall=0.0, steps=1)
            for v in ('D', 'T')]
    (run/'jobs.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')
    args = ['--years', '1', '--core-years', '1', '--b-max-years', '0', '--limit', '1', '--run-dir', str(run)]
    before = source_manifest(run)
    assert mod.main(args) == 2
    assert mod.main([*args, '--analysis-dir', str(tmp_path/'bypass')]) == 2
    assert mod.main([*args, '--evaluate-only']) == 2
    out = tmp_path/'p21_analysis'
    assert mod.main([*args, '--evaluate-only', '--analysis-dir', str(out)]) == 0
    assert source_manifest(run) == before
    assert read_json(out/'summary.json')['source'] == before
    assert mod.main([*args, '--evaluate-only', '--analysis-dir', str(out)]) == 2
