import math

import numpy as np
import pytest

from submoon_research.contracts.w1 import W1Design, EnsembleRow, PRIMARY_SECONDS
from submoon_research.events.escape import EscapeTracker, radius_crossings
from submoon_research.sampling.inference import (
    aggregate, bounded_interval, compare_hypotheses, geometric_fraction,
    fit_geometry, evaluate_geometry, required_randomizations,
)
from submoon_research.sampling.design import DomainSpec, MEASURES


def test_design_rejects_silent_scope_change():
    for changes in ({'horizon_seconds': 1.0}, {'contact_buffer': 0.0}, {'holdout': ['himalia']},
                    {'production_allowed': True}, {'measures': [MEASURES[0]]}):
        with pytest.raises(ValueError):
            W1Design(design_id='test', **changes)


def rows_for(design):
    return [dict(host=h, scenario_id=design.scenario_id, measure=m, domain_id=h+'-v1',
                 randomization=k, seed=design.seed(k), index=i, weight=1.0,
                 horizon_seconds=PRIMARY_SECONDS, last_valid_time=PRIMARY_SECONDS,
                 run_status='completed', outcome='survived')
            for h in design.development for m in MEASURES
            for k in range(design.randomizations) for i in range(2**design.power)]


def test_denominator_pairing_and_unknown_bounds():
    design = W1Design(design_id='test', power=1, randomizations=2)
    rows = rows_for(design)
    rows[0].update(run_status='partial', outcome='unresolved', last_valid_time=1.0)
    result = aggregate(rows, design)
    assert result['iapetus', MEASURES[0]][0].tolist() == [0.5, 1.0]
    assert result['iapetus', MEASURES[0]][1].tolist() == [1.0, 1.0]
    for bad in (rows[:-1], rows+[rows[0]], [rows[0] | {'seed': 99}]+rows[1:],
                [rows[0] | {'weight': 2.0}]+rows[1:], [rows[0] | {'horizon_seconds': 1.0}]+rows[1:]):
        with pytest.raises(ValueError):
            aggregate(bad, design)
    with pytest.raises(ValueError):
        EnsembleRow.model_validate(rows[0] | {'outcome': 'host_contact'})


def test_intervals_zero_variance_and_simultaneity():
    one = bounded_interval(np.zeros(8))
    many = bounded_interval(np.zeros(8), comparisons=45)
    assert 0 < one['upper'] < many['upper']
    assert bounded_interval([0, 0], [1, 1])['upper'] == 1
    assert required_randomizations() == math.ceil(math.log(40)/0.0002)
    with pytest.raises(ValueError):
        bounded_interval([float('nan'), 0])


def test_hypotheses_refutation_and_unknowns():
    d = W1Design(design_id='test')
    def values(value):
        return np.full(100000, value), np.full(100000, value)
    estimates = {(h, m): values(0.8 if h in d.main_hosts else 0.2)
                 for h in d.main_hosts+d.control_hosts for m in d.measures}
    result = compare_hypotheses(estimates, d)
    assert result['H1']['status'] == result['H2']['status'] == 'supported'
    estimates['iapetus', d.measures[0]] = values(0.95)
    estimates['iapetus', d.measures[1]] = values(0.2)
    assert compare_hypotheses(estimates, d)['H2']['status'] == 'contradicted'
    for m in d.measures:
        for h in d.main_hosts:
            estimates[h, m] = np.zeros(100000), np.ones(100000)
    assert compare_hypotheses(estimates, d)['H1']['status'] == 'inconclusive'
    with pytest.raises(ValueError):
        compare_hypotheses({}, d)


def test_geometry_fit_and_holdout_isolation():
    d = W1Design(design_id='test')
    domain = DomainSpec(domain_id='synthetic', gm_host=1.0, gm_parent=1000.0,
                        a_host_km=100.0, reference_radius_km=0.5, submoon_radius_km=0.1,
                        eccentricity_max=0.3, contact_buffer=0.02)
    domains = {h: domain for h in d.development}
    estimates = {(h, m): (np.full(8, geometric_fraction(domain, m, 0.5)),)*2
                 for h in d.development for m in d.measures}
    fit = fit_geometry(domains, estimates, d)
    assert fit['coefficient'] == 0.5
    with pytest.raises(ValueError):
        fit_geometry(domains | {'europa': domain}, estimates, d)
    with pytest.raises(ValueError):
        evaluate_geometry(domains, estimates, fit, d)
    for m in d.measures:
        assert 0 <= geometric_fraction(domain, m, 0.1) < geometric_fraction(domain, m, 0.9) <= 1


def dense_radial(position, velocity):
    def dense(t):
        t = np.asarray(t)
        y = np.zeros((6,)+t.shape)
        y[0] = position(t)
        y[3] = velocity(t)
        return y
    return dense


def test_escape_and_pending_window():
    dense = dense_radial(lambda t: 0.5+t, lambda t: 0*t+1)
    tracker = EscapeTracker(1, 2)
    assert tracker.advance(dense, 0, 3) is None
    assert tracker.outcome() == 'unresolved'
    snapshot = tracker.snapshot()
    resumed = EscapeTracker(**snapshot)
    event = resumed.advance(dense, 3, 4)
    assert event['time'] == pytest.approx(3.5)
    assert resumed.outcome() == 'operational_escape'
    assert not event['eternal_escape_proven']


def test_hidden_departure_and_return_in_one_step():
    dense = dense_radial(lambda t: 0.5+3*t*(1-t), lambda t: 3-6*t)
    assert len(radius_crossings(dense, 0, 1, 1)) == 2
    tracker = EscapeTracker(1, 2, outer_factor=1)
    assert tracker.advance(dense, 0, 1) is None
    assert tracker.returns == 1 and tracker.pending_since is None
    assert tracker.outcome() == 'survived'


def test_escape_event_before_later_return():
    dense = dense_radial(lambda t: 0.5+3*t*(1-t), lambda t: 3-6*t)
    tracker = EscapeTracker(1, 0.1, outer_factor=1)
    assert tracker.advance(dense, 0, 1)['time'] < 0.5


def test_event_history_cannot_skip_interval():
    tracker = EscapeTracker(1, 2)
    with pytest.raises(ValueError):
        tracker.advance(dense_radial(lambda t: 1+t, lambda t: 1+0*t), 1, 2)


def test_freeze_rejects_short_real_benchmark(tmp_path):
    import hashlib
    import json
    from submoon_research.contracts.freeze import validate_freeze
    spec = W1Design(design_id='test', status='ready_for_freeze')
    manifest = dict(status='completed', validation={'status': 'passed'},
        data_kind='real_nominal_cost_benchmark', resources={'integration_calls': 36},
        scientific_admission=dict(audited_physics=True, validated_generator=True,
                                  validated_dynamics=True, validated_events=True))
    inputs = []
    for name, content in [('manifest.json', manifest), ('cost.json', {}), ('variance.json', {})]:
        data = json.dumps(content).encode()
        (tmp_path/name).write_bytes(data)
        inputs.append(dict(path=name, sha256=hashlib.sha256(data).hexdigest()))
    design = dict(status='ready_for_freeze', inputs=inputs, w1_design=spec.model_dump(),
        pilot=dict(manifest='manifest.json', cost_evidence='cost.json', variance_evidence='variance.json'))
    for key in ('generator','domain','measures','hypotheses','split','predictions',
                'comparisons','intervals','stopping','failures','horizons','numeric_protocol'):
        design[key] = {'placeholder_for_rejection_test': True}
    with pytest.raises(ValueError, match='Короткий benchmark'):
        validate_freeze(tmp_path, design)
    (tmp_path/'cost.json').write_text('tampered')
    with pytest.raises(ValueError, match='SHA-256'):
        validate_freeze(tmp_path, design)


def test_pilot_admission_denies_draft_and_over_budget():
    from submoon_research.workflows.w1_ensemble import validate_pilot_admission
    d = W1Design(design_id='test')
    with pytest.raises(ValueError):
        validate_pilot_admission({}, d, {'pilot_wall_seconds': 600})
    admission = dict(status='validated', design_id=d.design_id, horizon_seconds=d.horizon_seconds,
        integrator='DOP853', audited_inputs=True, events_validated=True,
        long_dynamics_validated=True, restart_validated=True, estimated_total_wall_seconds=21601)
    with pytest.raises(ValueError, match='ресурсов'):
        validate_pilot_admission(admission, d, {'pilot_wall_seconds': 600})


def test_timeout_keeps_all_planned_rows_and_stops_once(tmp_path, monkeypatch):
    import hashlib
    import json
    from submoon_research.provenance import InputLedger
    from submoon_research.workflows import w1_ensemble
    d = W1Design(design_id='test', power=1, randomizations=2)
    domain = DomainSpec(domain_id='synthetic', gm_host=1., gm_parent=1000., a_host_km=100.,
        reference_radius_km=0.5, submoon_radius_km=0.1, eccentricity_max=0.3, contact_buffer=0.02)
    setups = {h: dict(domain=domain, basis=np.eye(3), relative=np.zeros((1,6)),
        gms=[1.], figures=[], radii=[0.6], host_period=100.) for h in d.development}
    report = tmp_path/'check.json'
    report.write_text(json.dumps({'status':'passed'}))
    admission = dict(status='validated', design_id=d.design_id, horizon_seconds=d.horizon_seconds,
        integrator='DOP853', audited_inputs=True, events_validated=True, long_dynamics_validated=True,
        restart_validated=True, estimated_total_wall_seconds=1.,
        evidence_sha256={'check.json':hashlib.sha256(report.read_bytes()).hexdigest()},
        numeric_options=dict(rtol=1e-11, atol_position=1e-10, atol_velocity=1e-12, max_step_period_fraction=0.01))
    class Run:
        folder = tmp_path
        resources = {'pilot_wall_seconds':600}
        ledger = InputLedger(tmp_path)
        manifest = {'resources':{}}
        run_id = 'synthetic-fixture'
        def check_budget(self):
            pass
        def validate(self, checks, **kwargs):
            self.checks = checks
    for name in ('results','checkpoints','initial_conditions'):
        (tmp_path/name).mkdir()
    calls = []
    def failure(*args, **kwargs):
        calls.append(1)
        raise TimeoutError('Превышен лимит времени')
    monkeypatch.setattr(w1_ensemble, 'integrate', failure)
    run = Run()
    outcomes, summary = w1_ensemble.execute(run, d, setups, admission)
    assert len(calls) == 1
    assert len(outcomes) == 36 and all(x['outcome'] == 'unresolved' for x in outcomes)
    assert all(x['interval']['lower'] == 0 and x['interval']['upper'] == 1 for x in summary)
    assert not run.checks['full_horizon_completed']
