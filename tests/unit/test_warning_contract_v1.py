"""Синтетические тесты политики; не проверяют полноту реального capture."""
import copy

import pytest

from submoon_research.diagnostics import warning_contract_v1 as w

FINGERPRINT = w.policy_fingerprint('a' * 64, 'b' * 64)
WARNING = dict(category='RuntimeWarning', message='IAS15 iterations', phase='cache', time=2.0)


def assess(**kwargs):
    args = dict(engine_status='completed', capture_complete=True, overflow=False,
                events=[], policy_sha256=FINGERPRINT)
    args.update(kwargs)
    return w.assess(**args)


def test_clean_and_failed_or_partial():
    assert assess()['assessment']['physical_outcome_policy'] == 'preserve_engine_outcome'
    for status in ('failed', 'partial'):
        result = assess(engine_status=status)
        assert not result['assessment']['admissible']
        assert result['assessment']['physical_outcome_policy'] == 'unresolved'


@pytest.mark.parametrize('mutation', [dict(events=[WARNING]), dict(capture_complete=False), dict(overflow=True)])
def test_warning_unknown_and_overflow_fail_closed(mutation):
    result = assess(**mutation)
    assert not result['assessment']['admissible']
    assert result['assessment']['physical_outcome_policy'] == 'unresolved'


def test_restart_retains_warning_and_failed_capture():
    parent = assess(engine_status='partial', events=[WARNING], capture_complete=False, overflow=True)
    old = copy.deepcopy(parent)
    continued = assess(prior=parent, expected_prior_sha256=w.digest(parent))
    assert continued['events'] == [WARNING] and continued['overflow']
    assert not continued['capture_complete'] and not continued['assessment']['admissible']
    assert parent == old


def test_clean_partial_can_resume_if_full_capture():
    parent = assess(engine_status='partial')
    continued = assess(prior=parent, expected_prior_sha256=w.digest(parent))
    assert continued['assessment']['admissible']


def test_restart_rejects_missing_tampered_or_other_policy():
    parent = assess(engine_status='partial', events=[WARNING])
    with pytest.raises(ValueError):
        assess(prior=parent)
    with pytest.raises(ValueError):
        assess(expected_prior_sha256=w.digest(parent))
    bad = copy.deepcopy(parent)
    bad['events'] = []
    with pytest.raises(ValueError):
        assess(prior=bad, expected_prior_sha256=w.digest(parent))
    with pytest.raises(ValueError):
        assess(prior=parent, expected_prior_sha256=w.digest(parent), policy_sha256='c' * 64)
    # Даже с пересчитанным SHA внутренне противоречивый ledger отвергается.
    with pytest.raises(ValueError):
        assess(prior=bad, expected_prior_sha256=w.digest(bad))


@pytest.mark.parametrize('event', [
    dict(WARNING, phase='bad'), dict(WARNING, time=float('nan')), dict(WARNING, time=-1),
    dict(WARNING, category=''), dict(WARNING, time=True), dict(WARNING, extra='unexpected'),
])
def test_bad_event_rejected(event):
    with pytest.raises(ValueError):
        assess(events=[event])


@pytest.mark.parametrize('status', ['completed', 'failed'])
def test_terminal_ledger_cannot_resume(status):
    parent = assess(engine_status=status)
    with pytest.raises(ValueError, match='partial'):
        assess(prior=parent, expected_prior_sha256=w.digest(parent))


def test_policy_and_capture_implementation_bound():
    assert w.policy_fingerprint('a' * 64, 'b' * 64) != w.policy_fingerprint('a' * 64, 'c' * 64)
    with pytest.raises(ValueError):
        w.policy_fingerprint('NOT_SHA', 'b' * 64)
