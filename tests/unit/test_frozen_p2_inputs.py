"""OPT-06: frozen-приёмка не вызывает генератор; внешний якорь обязателен."""
import copy
import json

import pytest

from submoon_research.contracts.frozen_p2 import (
    check_generator, load_frozen_inputs, save_frozen_inputs,
)
from submoon_research.contracts.p2_files import file_digest, source_manifest
from test_p2_file_contracts import ARGS, load
from test_p2_file_contracts import document as document  # noqa: F401
from test_p2_file_contracts import stand as stand  # noqa: F401


def test_run_and_acceptance_without_generator(stand, document, monkeypatch, tmp_path):
    inputs = tmp_path/'inputs'
    sha = save_frozen_inputs(inputs, document, dict(kind='synthetic-test'))
    path = inputs/'inputs_manifest.json'
    anchor = ['--inputs-manifest', str(path), '--inputs-manifest-sha256', sha]
    before = source_manifest(inputs)
    def forbidden(*args, **kwargs):
        raise AssertionError('генератор не должен вызываться')
    monkeypatch.setattr(stand, 'units', forbidden)
    run = tmp_path/'run'
    assert stand.main([*ARGS, '--run-dir', str(run), *anchor]) == 0
    verifier = load('verify_p2_2_run')
    monkeypatch.setattr(verifier, 'stand_module', lambda: stand)
    args = verifier.parse(['--run', str(run), '--per-cell', '1', '--b-per-cell', '0',
                          '--years', '0.0003', '--b-years', *anchor])
    report = verifier.verify(args)
    assert report['passed'], report['failures']
    assert report['checks']['input_basis'] == 'frozen; generator not called'
    assert source_manifest(inputs) == before
    assert (run/'inputs_manifest.json').exists()
    assert stand.main([*ARGS, '--run-dir', str(inputs/'nested'), *anchor]) == 2
    assert source_manifest(inputs) == before
    assert stand.main([*ARGS, '--run-dir', str(run), *anchor, '--evaluate-only',
                       '--analysis-dir', str(inputs/'analysis')]) == 2
    assert source_manifest(inputs) == before
    analysis = tmp_path/'analysis'
    assert stand.main([*ARGS, '--run-dir', str(run), *anchor, '--evaluate-only', '--analysis-dir', str(analysis)]) == 0


@pytest.mark.parametrize('mutation', ['document', 'manifest', 'missing_anchor', 'wrong_anchor', 'params'])
def test_frozen_trust_failures(document, tmp_path, mutation):
    inputs = tmp_path/'inputs'
    sha = save_frozen_inputs(inputs, document, dict(kind='synthetic-test'))
    path = inputs/'inputs_manifest.json'
    params = copy.deepcopy(document['params'])
    if mutation == 'document':
        (inputs/'units.json').write_text(json.dumps(dict(document, units=[])), encoding='utf-8')
    elif mutation == 'manifest':
        doc = json.loads(path.read_text(encoding='utf-8'))
        doc['provenance'] = dict(kind='forged')
        path.write_text(json.dumps(doc), encoding='utf-8')
    elif mutation == 'missing_anchor':
        sha = None
    elif mutation == 'wrong_anchor':
        sha = '0'*64
    else:
        params['unit_start'] = 2
    with pytest.raises(ValueError):
        load_frozen_inputs(path, sha, params=params)


def test_generator_check_is_separate(document, tmp_path):
    inputs = tmp_path/'inputs'
    sha = save_frozen_inputs(inputs, document, dict(kind='synthetic-test'))
    path = inputs/'inputs_manifest.json'
    assert check_generator(path, sha, lambda old: old)['passed']
    altered = copy.deepcopy(document)
    altered['units'][0]['state'][-1] += 1e-8
    assert not check_generator(path, sha, lambda old: altered)['passed']
    assert file_digest(path) == sha


def test_diagnostic_identity_fails_before_execution(stand, monkeypatch, tmp_path):
    # Synthetic source includes B, but no native step or REBOUNDx is used here.
    run = tmp_path/'source'
    args = ['--per-cell', '1', '--b-per-cell', '1', '--years', '0.0003',
            '--b-years', '0.0003', '--workers', '0', '--run-dir', str(run)]
    assert stand.main(args) == 0
    diag = load('diag_b_failure_p2_2')
    monkeypatch.setattr(diag, 'stand', stand)
    def forbidden(*a, **kw):
        raise AssertionError('интеграция не разрешена при несовпадении')
    monkeypatch.setattr(stand, '_job', forbidden)
    unit = stand.units(1)[0]
    opts = diag.parse(['--run', str(run), '--source-summary-sha256', file_digest(run/'summary.json'),
                      '--source-jobs-sha256', file_digest(run/'jobs.jsonl'),
                      '--orbit', unit['orbit_id'], '--years', '0.0003', '--output', str(tmp_path/'diag.json')])
    assert all(diag.prepare_identity(opts)[3]['checks'].values())
    monkeypatch.setattr(stand, 'b_fingerprint', lambda: 'other-B')
    before = source_manifest(run)
    with pytest.raises(ValueError, match='идентичность'):
        diag.main(vars_to_argv(opts))
    assert source_manifest(run) == before and not (tmp_path/'diag.json').exists()


def vars_to_argv(options):
    out = []
    for key, value in vars(options).items():
        if value is not None and value is not False:
            out += ['--'+key.replace('_', '-')]
            if value is not True:
                out += [str(value)]
    return out
