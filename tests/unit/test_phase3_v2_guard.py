"""Синтетические проверки W2-T065: без интеграций и научного допуска."""
import copy

import pytest

from submoon_research.diagnostics import convergence_v2_guard as v2
from test_phase3_audit import DIRECTIONS, SCALES, THR, case, converging_case, row


def classify(c, archive_b=None):
    return v2.classify(c, THR, DIRECTIONS, archive_b=archive_b)


def test_complete_k1_and_archive_control():
    c = converging_case()
    assert classify(c)['cls'] == 'K1'
    del c['rows']['B'], c['rows']['Bs'], c['rows']['Be']
    assert classify(c)['cls'] == 'K4'
    result = classify(c, row())
    assert result['cls'] == 'K1' and result['confidence'] == 'medium'
    assert result['original']['ias15']['status'] == 'insufficient'


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'unknown', 'failed', 'extra'])
def test_incomplete_unknown_duplicate_cannot_give_k1(mutation):
    c = converging_case()
    if mutation == 'missing':
        c['perturb_rows'].pop()
    elif mutation == 'duplicate':
        c['perturb_rows'].append(copy.deepcopy(c['perturb_rows'][0]))
    elif mutation == 'extra':
        c['perturb_rows'][0]['perturbation']['direction'] = 'unexpected'
    else:
        c['perturb_rows'][0].update(row('unresolved', status='failed' if mutation == 'failed' else 'completed'))
    result = classify(c)
    assert result['cls'] == 'K4' and not result['perturbation_grid']['complete']


@pytest.mark.parametrize('scales,directions', [([], DIRECTIONS), (SCALES, []), ([1e-10, 1e-10], DIRECTIONS),
                                                (SCALES, ['+R', '+R']), ([-1.0], DIRECTIONS)])
def test_invalid_perturbation_contract_rejected(scales, directions):
    c = converging_case()
    c['scales'] = scales
    with pytest.raises(ValueError):
        v2.perturbation_grid(c, directions)


def test_contradictory_single_local_or_archive_b():
    c = converging_case()
    del c['rows']['Bs'], c['rows']['Be']
    c['rows']['B'] = row('operational_escape')
    assert classify(c)['cls'] == 'K4'
    del c['rows']['B']
    assert classify(c, row('operational_escape'))['cls'] == 'K4'
    assert classify(c, row('unresolved'))['cls'] == 'K4'


@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_unknown_cannot_establish_physical_k3(status):
    c = case({code: row() if code == 'U' else row('operational_escape')
              for code in ('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be')})
    for p in c['perturb_rows']:
        p.update(row('unresolved', status=status))
    result = classify(c)
    assert result['cls'] == 'K4'
    assert result['physical_sensitivity']['1e-12']['physical_flips'] == 0
    assert result['physical_sensitivity']['1e-12']['unknown'] == 10


def test_high_e_confidence_capped():
    rows = {code: row('unresolved') for code in ('D', 'Ds', 'T', 'Ts', 'U')}
    rows.update(B=row(), Bs=row(), Be=row('operational_escape'))
    c = case(rows, base='B', high_e=True)
    for p in c['perturb_rows']:
        if p['perturbation']['direction'] in ('+R', '-R'):
            p.update(row('operational_escape'))
    result = classify(c)
    assert result['cls'] == 'K3' and result['confidence'] == 'medium'
    assert 'high_e_confidence_capped' in result['flags']


def test_time_nonconvergence_without_label_not_k3():
    c = case({code: row('operational_escape', time=t) for code, t in
              zip(('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be'), (100., 90., 80., 70., 60., 95., 85., 75.))})
    for index, p in enumerate(c['perturb_rows']):
        p['event']['time'] += 20 * (index % 10)
    result = classify(c)
    assert result['original']['cls'] == 'K3' and result['cls'] == 'K4'
    assert result['physical_sensitivity']['1e-12']['spread_p0'] == 180


def test_common_time_shift_not_spread():
    c = case({code: row('operational_escape', time=t) for code, t in
              zip(('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be'), (100., 90., 80., 70., 60., 95., 85., 75.))})
    for p in c['perturb_rows']:
        p['event']['time'] += 100
    stats = v2.physical_sensitivity(c, THR, DIRECTIONS)
    assert stats[f'{SCALES[0]:g}']['spread_p0'] == 0
    assert stats[f'{SCALES[0]:g}']['max_shift_p0'] == 100
