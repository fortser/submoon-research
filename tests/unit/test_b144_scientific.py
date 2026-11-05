"""Synthetic checks: denominators, exact time matching, events, duplicate rejection."""
from typing import Any

import pytest

from submoon_research.diagnostics.b144_scientific import (
    YEAR, category, event_comparison, index_rows, outcome_counts, quantiles,
    sample_comparison, sampled_orbital,
)


def row(**kwargs) -> dict[str, Any]:
    return dict(orbit_id='synthetic:0000', years=100, variant='B',
                run_status='completed', outcome='survived', **kwargs)


def test_duplicate_rejected():
    with pytest.raises(ValueError, match='duplicate'):
        index_rows([row(), row()])


def test_failed_is_not_physical_loss():
    r = row()
    r.update(run_status='failed', outcome='host_contact')
    assert category(r) == 'unresolved'
    stats = outcome_counts([row(), r])
    assert stats['n'] == 2
    assert stats['survival_identification_bounds'] == [.5, 1.]
    assert stats['computational'] == 1


def test_high_e_not_removed_from_denominator():
    r = row(unresolved_reason='high_e_prolonged')
    r['outcome'] = 'unresolved'
    stats = outcome_counts([row(), r])
    assert stats['high_e'] == 1
    assert stats['survival_identification_bounds'] == [.5, 1.]


def test_exact_common_times_no_interpolation():
    a = row(samples=[[0, [1, 0, 0, 0, 1, 0]], [YEAR, [2, 0, 0, 0, 1, 0]]])
    b = row(samples=[[0, [1, 0, 0, 0, 1, 0]], [YEAR+1, [8, 0, 0, 0, 1, 0]]])
    u = dict(a_km=1, period_seconds=1)
    s = sample_comparison(a, b, u)
    assert s['common_samples'] == 1
    assert s['first_observed_crossing_years'] is None
    assert not s['continuous_crossing_known']
    b['samples'][1][0] = YEAR
    s = sample_comparison(a, b, u)
    assert s['first_observed_crossing_years'] == 1
    assert s['max_dr_over_a'] == 6


def test_event_body_and_type_must_match():
    a = row(event=dict(event='reference_contact', body=0, time=4))
    b = row(event=dict(event='reference_contact', body=1, time=8))
    assert event_comparison(a, b, 2) is None
    b['event']['body'] = 0
    comparison = event_comparison(a, b, 2)
    assert comparison is not None
    assert comparison['abs_dt_over_P0'] == 2
    b['event']['event'] = 'operational_escape'
    assert event_comparison(a, b, 2) is None


def test_bound_circular_sample():
    r = row(samples=[[0, [1, 0, 0, 0, 1, 0]]])
    q = sampled_orbital(r, 1)
    assert q['max_e_sampled'] == 0
    assert q['bound_high_e_samples'] == 0
    assert q['two_body_energy_range_km2_s2'] == [-.5, -.5]


def test_empty_quantiles_do_not_fabricate_zero():
    assert quantiles([None, float('nan')])['median'] is None
    assert quantiles([1, 2, 3])['median'] == 2


@pytest.fixture(scope='module')
def archive_inputs():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    run = root/'runs/W2-B144-57496493'
    if not run.exists():
        pytest.skip('Local immutable research archive is not distributed with code')
    manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    units = json.loads((run/'units.json').read_text(encoding='utf-8'))['units']
    new = index_rows([json.loads(line) for line in (run/'jobs.jsonl').read_text().splitlines()])
    old_path = root/'runs/W2-P2-2a-56220810/jobs.jsonl'
    old = index_rows([json.loads(line) for line in old_path.read_text().splitlines()], True)
    reference = json.loads((root/'data/manifests/W2_B144_reference_v1.json').read_text())
    return new, manifest, {u['orbit_id']: u for u in units}, old, reference


def test_actual_archive_passes_independent_validation(archive_inputs):
    from submoon_research.diagnostics.b144_scientific import independently_validate
    assert independently_validate(*archive_inputs)['archived_fingerprints'] == 864


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('run_status', 'unknown', 'completed'),
    ('job_fingerprint', 'tampered', 'fingerprint'),
    ('last_valid_time', -1, 'invalid time'),
    ('final_probe', [float('nan')]*6, 'nonfinite'),
])
def test_corrupted_copy_rejected(archive_inputs, field, value, message):
    from copy import deepcopy
    from submoon_research.diagnostics.b144_scientific import independently_validate
    new, *rest = archive_inputs
    corrupted = dict(new)
    k = next(iter(new))
    corrupted[k] = deepcopy(new[k])
    corrupted[k][field] = value
    with pytest.raises(ValueError, match=message):
        independently_validate(corrupted, *rest)
