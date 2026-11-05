"""W2-T064: аудит и минимальные контрпримеры замороженного классификатора.

xfail(strict=True) документирует дефекты v1, а не меняет правила после раскрытия данных.
Тесты не интегрируют орбиты и не изменяют завершённые runs.
"""
import copy
from typing import Any

import pytest

from submoon_research.diagnostics import convergence as cv
from submoon_research.diagnostics.phase3_audit import perturb_stats, strict_loads

THR = dict(event_time_tight_p0=1e-6, event_time_p0=1.0, contraction=0.1,
           sigma_num_window_years=1.0, sensitive_scale_floor=1e-10,
           k2_event_margin_p0=10.0, k3_min_directions=2, k3_time_spread_p0=10.0)
SCALES = [1e-12, 1e-10, 1e-8]
DIRECTIONS = ['+R', '-R', '+T', '-T', '+N', '-N', 'I0', 'I1', 'I2', 'I3']


def row(outcome='survived', time=100.0, status='completed'):
    return dict(outcome=outcome, years=1000.0, last_valid_time=time, run_status=status,
                unresolved_reason='high_e_prolonged' if outcome == 'unresolved' else None,
                event=dict(time=time, event='operational_escape') if outcome == 'operational_escape' else None)


def case(rows, base='Ts', high_e=False) -> dict[str, Any]:
    pert = [dict(copy.deepcopy(rows[base]), perturbation=dict(scale=s, direction=d))
            for s in SCALES for d in DIRECTIONS]
    return dict(rows=rows, dense={}, ladders=dict(dop853=['D', 'Ds', 'T', 'Ts', 'U'], ias15=['B', 'Bs', 'Be']),
                p0=1.0, a0=1.0, n0=1.0, perturb_family=base, perturb_rows=pert, scales=SCALES, high_e=high_e)


def converging_case():
    return case({code: row('operational_escape') if code == 'D' else row()
                 for code in ('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be')})


def test_audit_rejects_duplicate_json_keys_and_nonfinite():
    for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}'):
        with pytest.raises(ValueError):
            strict_loads(text)


def test_audit_separates_unknown_from_physical_flips():
    base = row()
    rows = [dict(row(outcome), perturbation=dict(scale=1e-12, direction=d)) for outcome, d in
            [('unresolved', '+R'), ('operational_escape', '-R'), ('survived', '+T')]]
    result = perturb_stats(base, rows, SCALES, 1.0)['1e-12']
    assert result['n'] == 3 and result['unique_directions'] == 3
    assert result['physical_flips'] == 1 and result['unresolved_or_incomplete'] == 1


def test_audit_distinguishes_time_spread_from_common_shift():
    base = row('operational_escape', time=100.0)
    rows = [dict(row('operational_escape', time=200.0), perturbation=dict(scale=1e-12, direction=d))
            for d in DIRECTIONS]
    result = perturb_stats(base, rows, SCALES, 1.0)['1e-12']
    assert result['max_abs_dt_p0'] == 100.0 and result['same_outcome_range_p0'] == 0.0


@pytest.mark.xfail(strict=True, reason='v1: число направлений/пустые масштабы не проверяются перед K1')
def test_k1_requires_complete_perturbation_grid():
    c = converging_case()
    c['perturb_rows'] = c['perturb_rows'][:1]
    assert cv.classify(c, THR)['cls'] == 'K4'


@pytest.mark.xfail(strict=True, reason='v1: единственная противоречащая строка B игнорируется как insufficient')
def test_k1_cannot_ignore_available_contradictory_b():
    c = converging_case()
    del c['rows']['Bs'], c['rows']['Be']
    c['rows']['B'] = row('operational_escape')
    assert cv.classify(c, THR)['cls'] != 'K1'


@pytest.mark.xfail(strict=True, reason='v1: unknown/failed считает сменой физического исхода')
@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_unknowns_alone_cannot_establish_k3(status):
    c = case({code: row() if code == 'U' else row('operational_escape')
              for code in ('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be')})
    c['perturb_rows'] = [dict(row('unresolved', status=status), perturbation=p['perturbation'])
                         for p in c['perturb_rows']]
    assert cv.classify(c, THR)['cls'] == 'K4'


@pytest.mark.xfail(strict=True, reason='v1: YAML требует уверенность high-e не выше medium, K3 возвращает high')
def test_high_e_confidence_cap():
    rows = {code: row('unresolved') for code in ('D', 'Ds', 'T', 'Ts', 'U')}
    rows.update(B=row(), Bs=row(), Be=row('operational_escape'))
    c = case(rows, base='B', high_e=True)
    for p in c['perturb_rows']:
        if p['perturbation']['direction'] in ('+R', '-R'):
            p.update(row('operational_escape'))
    result = cv.classify(c, THR)
    assert result['cls'] == 'K3' and result['confidence'] != 'high'


@pytest.mark.xfail(strict=True, reason='v1: K3 использует несходимость времени вместо несходимости метки (§6)')
def test_k3_distinguishes_time_only_from_label_nonconvergence():
    c = case({code: row('operational_escape', time=t) for code, t in
              zip(('D', 'Ds', 'T', 'Ts', 'U', 'B', 'Bs', 'Be'), (100., 90., 80., 70., 60., 95., 85., 75.))})
    for index, p in enumerate(c['perturb_rows']):
        p['event']['time'] += 20 * (index % 10)
    assert cv.classify(c, THR)['cls'] == 'K4'
