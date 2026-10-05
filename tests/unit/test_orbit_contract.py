import pytest

from submoon_research.contracts.orbit import Outcome


def test_compute_failure_cannot_be_reported_as_escape():
    base=dict(run_status='failed',terminal_event=None,event_time=None,last_valid_time=2.0,uncertainty_status='unresolved')
    with pytest.raises(ValueError):
        Outcome(**base,physical_outcome='permanent_escape')
    assert Outcome(**base,physical_outcome='unresolved').last_valid_time==2.0


def test_event_cannot_be_beyond_valid_state():
    with pytest.raises(ValueError):
        Outcome(run_status='completed',physical_outcome='host_contact',terminal_event='reference_contact',
            event_time=3.0,last_valid_time=2.0,uncertainty_status='resolved')
