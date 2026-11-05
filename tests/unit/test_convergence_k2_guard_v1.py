"""Отрицательные контроли K2: синтетика, без интеграций и допуска."""
import copy

import numpy as np
import pytest

from submoon_research.diagnostics import convergence_k2_guard_v1 as guard
from submoon_research.diagnostics import convergence_v2_guard as prior
from test_phase3_audit import DIRECTIONS, THR, case, converging_case, row


def k2_case(outcome='unresolved', status='completed'):
    rows = {c: row('operational_escape', time=120.) for c in ('D', 'Ds', 'T', 'Ts', 'U')}
    rows['D'] = row(outcome, time=115., status=status)
    c = case(rows)
    t = np.array([0., 110.])
    p = np.array([[1., 0., 0., 0., 1., 0.], [1., 0., 0., 0., 1., 0.]])
    c['dense'] = {code: (t.copy(), p.copy()) for code in rows}
    return c


@pytest.mark.parametrize('outcome,status', [('unresolved', 'completed'), ('unresolved', 'failed')])
def test_unknown_and_failed_cannot_produce_k2(outcome, status):
    c = k2_case(outcome, status)
    assert prior.classify(c, THR, DIRECTIONS)['cls'] == 'K2'
    result = guard.classify(c, THR, DIRECTIONS)
    assert result['cls'] == 'K4' and result['confidence'] is None
    assert result['k2_status'] == 'insufficient_evidence'
    assert 'k2_unknown_or_failed_outcome' in result['flags']
    assert result['original']['k2'][0]['covered']


def test_known_outcomes_one_saved_point_still_not_proof():
    c = k2_case('operational_escape')
    result = guard.classify(c, THR, DIRECTIONS)
    assert result['prior_v2_class'] == 'K2'
    assert result['cls'] == 'K4'
    assert 'k2_event_coverage_not_validated' in result['flags']
    assert 'k2_unknown_or_failed_outcome' not in result['flags']


def test_other_classes_and_input_not_changed():
    c = converging_case()
    previous = copy.deepcopy(c)
    old = prior.classify(c, THR, DIRECTIONS)
    new = guard.classify(c, THR, DIRECTIONS)
    assert c == previous
    assert (new['cls'], new['confidence']) == (old['cls'], old['confidence'])
    assert new['k2_status'] == 'not_assessed'
