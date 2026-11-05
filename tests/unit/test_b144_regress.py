"""W2-T050: unit checks of the 144-unit B regression runner (no archive, no rental)."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from test_p2_file_contracts import load

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def b144():
    return load('b144_regress')


def reference(entries):
    return dict(implementations=dict(compiled='c', B='old'), jobs=entries)


def entry(orbit, years, wall, fp='fp', probe=(1., 2., 3., 4., 5., 6.)):
    return dict(orbit_id=orbit, years=years, job_fingerprint=fp, initial_probe=list(probe), archived_wall=wall)


def test_check_identity_is_exact(b144):
    units = [dict(orbit_id='a', state=[0.]*6+[1., 2., 3., 4., 5., 6.]), dict(orbit_id='b', state=[0.]*12)]
    ref = reference([entry('a', 100., 1.), entry('b', 100., 1.), entry('c', 100., 1.)])
    out = b144.check_identity(units, ref, job_fingerprint=lambda u, y, v, impl: 'fp')
    assert {(m['orbit_id'], m.get('reason')) for m in out} == {('b', None), ('c', 'missing unit')}
    assert b144.check_identity(units[:1], reference([entry('a', 100., 1.)]),
                               job_fingerprint=lambda u, y, v, impl: 'other')[0]['job_fingerprint'] is False


def test_plan_order_longest_first(b144):
    ref = reference([entry('a', 100., 5.), entry('b', 1000., 50.), entry('c', 100., 0.1), entry('a', 1000., 50.)])
    assert b144.plan_order(ref) == [('a', 1000.), ('b', 1000.), ('a', 100.), ('c', 100.)]


def test_predict_uses_tail_and_volume(b144):
    ref = reference([entry('a', 1000., 4*3600.), entry('b', 100., 3*3600.), entry('c', 100., 3*3600.)])
    p = b144.predict([1.0, 1.2, 0.9], ref, workers=32, margin=1.25)
    assert p['ratio'] == 1.0 and p['predicted_longest_s'] == 4*3600.
    assert p['predicted_wall_s'] == 4*3600. and p['with_margin_s'] == 5*3600.  # tail-bound
    p = b144.predict([2.0], ref, workers=2, margin=1.)
    assert p['predicted_longest_s'] == 8*3600. and p['predicted_wall_s'] == 10*3600.  # volume-bound


def row(orbit, years, status='completed', event=None, last=None, fp='e'):
    return dict(orbit_id=orbit, years=years, run_status=status, event=event, job_fingerprint=fp,
                last_valid_time=years if last is None else last, outcome='survived', unresolved_reason=None)


def test_evaluate_pre_registered_rule(b144):
    planned = [('a', 1.), ('b', 1.)]
    fp = {k: 'e' for k in planned}
    hz = {k: k[1] for k in planned}
    assert b144.evaluate([row('a', 1.), row('b', 1.)], planned, fp, hz)['verdict'] == 'PASSED'
    assert b144.evaluate([row('a', 1.), row('b', 1., 'failed')], planned, fp, hz)['verdict'] == 'NOT_PASSED'
    assert b144.evaluate([row('a', 1.), row('b', 1., 'partial')], planned, fp, hz)['verdict'] == 'INCOMPLETE'
    assert b144.evaluate([row('a', 1.)], planned, fp, hz)['verdict'] == 'INCOMPLETE'
    assert b144.evaluate([row('a', 1.), row('b', 1., fp='x')], planned, fp, hz)['verdict'] == 'INCOMPLETE'
    assert b144.evaluate([row('a', 1.), row('b', 1., last=0.5)], planned, fp, hz)['verdict'] == 'INCOMPLETE'
    terminal = row('b', 1., event=dict(event='host_contact', body=0, time=0.5), last=0.5)
    assert b144.evaluate([row('a', 1.), terminal], planned, fp, hz)['verdict'] == 'PASSED'
    assert b144.evaluate([row('a', 1.), row('a', 1.), row('b', 1.)], planned, fp, hz)['verdict'] == 'INCOMPLETE'
    passed = b144.evaluate([row('a', 1.), row('b', 1.)], planned, fp, hz)
    assert math.isclose(passed['failure_upper_95'], 1-0.05**(1/2))


def test_cp_upper_zero_failures(b144):
    assert math.isclose(b144.cp_upper(0, 288), 1-0.05**(1/288))
    assert 0.0102 < b144.cp_upper(0, 288) < 0.0105
    assert b144.cp_upper(0, 0) is None


def test_compare_rows_labels_and_event_time(b144):
    a = dict(outcome='host_contact', unresolved_reason=None, event=dict(event='host_contact', body=0, time=10.))
    b = dict(outcome='host_contact', unresolved_reason=None, event=dict(event='host_contact', body=0, time=10.5))
    c = dict(outcome='unresolved', unresolved_reason='high_e_prolonged', event=None)
    assert b144.compare_rows(a, b) == dict(same_label=True, new=['host_contact', None],
                                           other=['host_contact', None], event_dt_s=0.5)
    assert b144.compare_rows(a, c)['same_label'] is False and b144.compare_rows(a, c)['event_dt_s'] is None


def test_reference_document_is_consistent(b144):
    doc = json.loads(b144.REFERENCE.read_text(encoding='utf-8'))
    assert doc['schema'] == 'w2-b144-reference-v1' and len(doc['jobs']) == 288
    assert doc['source_summary_sha256'] == b144.ARCHIVE_SUMMARY_SHA and doc['source_jobs_sha256'] == b144.ARCHIVE_JOBS_SHA
    hosts = {}
    for e in doc['jobs']:
        hosts[e['orbit_id'].split(':')[0]] = hosts.get(e['orbit_id'].split(':')[0], 0)+1
        assert e['years'] in b144.YEARS and 0 <= e['unit'] < b144.B_UNITS and len(e['initial_probe']) == 6
    assert hosts == dict(ganymede=96, himalia=96, iapetus=96)
    assert {(o, y) for o, y in b144.BENCHMARK} <= {(e['orbit_id'], e['years']) for e in doc['jobs']}
    assert sum(e['archived_status'] == 'failed' for e in doc['jobs']) == 1


def test_devcheck_refuses_outside_scratch(b144, tmp_path):
    with pytest.raises(ValueError, match='scratch'):
        b144.devcheck(tmp_path/'x', 'b', 'r', 1)


GMS = [1., 1000.]
INITIAL = np.array([[0., 0., 0., 0., 0., 0.], [100., 0., 0., 0., math.sqrt(10.01), 0.],
                    [1., 0., 0., 0., 1., 0.02]])


def test_monitor_records_cheap_diagnostics(b144, monkeypatch):
    pytest.importorskip('reboundx')
    from submoon_research.dynamics import engine_compare
    monkeypatch.setattr(engine_compare, 'NativeIAS15', b144.Monitor)
    b144.Monitor.reset()
    out = engine_compare.integrate_engine('ias15_reboundx', INITIAL, GMS, [], [0.01, 1.], 400., period=2*math.pi)
    s = b144.Monitor.stats
    assert out['run_status'] == 'completed' and s['steps'] == out['steps'] > 1000
    assert 0 < s['max_endpoint_residual'] < 1e-9 and s['min_dt'] > 0 and s['min_dt_over_ulp_t'] > 1e6
    assert 0 < s['max_abs_coordinate_km'] < 250. and s['failure_context'] is None and s['diagnostic_error'] is None
    assert s['coordinate_samples'] == 1+s['steps']//b144.COORDINATE_EVERY
    ratio = b144.probe_cancellation(b144.Monitor.__mro__[1](INITIAL, GMS, []).sim, 3)
    assert 0 < ratio <= 1


def test_monitor_keeps_failure_context(b144, monkeypatch):
    pytest.importorskip('reboundx')
    from submoon_research.dynamics import native_ias15

    def broken(self, bound, max_step=np.inf):
        raise ArithmeticError('IAS15 нарушил временную границу')

    native = b144.Monitor(INITIAL, GMS, [], t0=5., initial_dt=1e-3)
    monkeypatch.setattr(native_ias15.NativeIAS15, 'step', broken)
    b144.Monitor.reset()
    with pytest.raises(ArithmeticError):
        native.step(10., 0.1)
    ctx = b144.Monitor.stats['failure_context']
    assert ctx['left'] == 5. and ctx['bound'] == 10. and ctx['max_step'] == 0.1 and ctx['ulp_t'] == math.ulp(5.)


def test_diagnostics_bug_does_not_become_b_failure(b144, monkeypatch):
    pytest.importorskip('reboundx')
    from submoon_research.dynamics import engine_compare

    def bad(self, *args):
        raise TypeError('diagnostics bug')

    monkeypatch.setattr(engine_compare, 'NativeIAS15', b144.Monitor)
    monkeypatch.setattr(b144.Monitor, '_account', bad)
    b144.Monitor.reset()
    out = engine_compare.integrate_engine('ias15_reboundx', INITIAL, GMS, [], [0.01, 1.], 20., period=2*math.pi)
    assert out['run_status'] == 'completed' and 'diagnostics bug' in b144.Monitor.stats['diagnostic_error']
