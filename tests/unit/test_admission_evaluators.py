"""Оценщики допуска W2-P004 (P1, P2-1): контрпримеры аудита W2-T009 (W2-I010, I011, I012)."""
import copy
import importlib.util
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p1 = _load('admission_p1_equivalence')
p2 = _load('admission_p2_horizons')
CASE = dict(orbit_id='synthetic', host='synthetic', period_seconds=2*math.pi, a_km=1.)


def _p1_row(samples, final_probe, end=1.0, event=True):
    return dict(periods=200., run_status='completed', error=None,
                outcome='host_contact' if event else 'survived', last_valid_time=end,
                event=dict(event='reference_contact', time=end, body=0) if event else None,
                samples=samples, final_probe=final_probe, steps=10, nfev=150,
                python_event_steps=1, wall=.1)


def test_p1_start_only_comparison_cannot_pass_different_end_states():
    # Контрпример аудита: общий только старт, конечные скорости 1 и 10.
    row = _p1_row([(0., [1., 0, 0, 0, 1, 0])], [.5, 0, 0, 0, 1, 0])
    other = copy.deepcopy(row)
    other['last_valid_time'] += 1e-10
    other['event']['time'] += 1e-10
    other['final_probe'][4] = 10.
    assert p1.compare(row, other, CASE)['C3'] is False


def test_p1_insufficient_coverage_is_inconclusive():
    row = _p1_row([(0., [1., 0, 0, 0, 1, 0])], [.5, 0, 0, 0, 1, 0])
    assert p1.compare(row, copy.deepcopy(row), CASE)['C3'] == 'inconclusive'


def test_p1_dense_coverage_passes_identical_runs():
    grid = p1.sample_grid(1.0)
    samples = [(float(t), [math.cos(t), math.sin(t), 0, -math.sin(t), math.cos(t), 0]) for t in grid]
    row = _p1_row(samples, samples[-1][1], end=1.0, event=False)
    assert p1.compare(row, copy.deepcopy(row), CASE)['C3'] is True


def test_p1_empty_or_reduced_set_is_not_passed():
    assert not p1.evaluate([], [], [], True)['passed']
    row = dict(orbit_id='synthetic', periods=20., C1=True, C2=True, C3=True)
    assert not p1.evaluate([row], ['synthetic'], [20.], False)['passed']
    assert not p1.evaluate([row], ['synthetic', 'missing'], [20.], True)['passed']
    assert p1.evaluate([row], ['synthetic'], [20.], True)['passed']


def _p2_row(variant):
    return dict(orbit_id='synthetic', variant=variant, run_status='completed', error=None,
                outcome='survived', event=None, last_valid_time=1.,
                samples=[(0., [1., 0, 0, 0, 1, 0]), (1., [1., 0, 0, 0, 1, 0])])


def test_p2_empty_incomplete_or_reduced_set_is_not_passed():
    case = dict(CASE)
    assert not p2.evaluate([], [case], 1.)['passed']
    assert not p2.evaluate([_p2_row('D'), _p2_row('T')], [case], 1.)['passed']  # нет B
    full = [_p2_row(v) for v in ('D', 'T', 'B')]
    assert p2.evaluate(full, [case], 1.)['passed']
    assert not p2.evaluate(full, [case], 1., full_scope=False)['passed']
    assert not p2.evaluate(full + [_p2_row('D')], [case], 1.)['passed']  # повтор


def test_p2_job_fingerprint_binds_implementation_mode_and_horizon():
    case = dict(CASE, state=[0.]*12, gms=[1.], figures=[], radii=[.01], hill_km=None, escape_window=None)
    impl = dict(compiled='a', B='b')
    base = p2.job_fingerprint(case, 1.0, 'D', impl)
    assert base == p2.job_fingerprint(case, 1.0, 'D', dict(impl))
    assert base != p2.job_fingerprint(case, 1.0, 'D', dict(impl, compiled='c'))
    assert base != p2.job_fingerprint(case, 1.0, 'T', impl)
    assert base != p2.job_fingerprint(case, 10.0, 'D', impl)
    assert p2.job_fingerprint(case, 1.0, 'B', impl) != p2.job_fingerprint(case, 1.0, 'B', dict(impl, B='x'))


# Стенд v4 (план §3.2, уточнение до удалённого запуска): обязательный набор и расширение,
# остановка сроком, сравнение на общем отрезке.

def _row(variant, end=1., status='completed', event_time=None, stopped=False):
    times = [t for t in (0., .25, .5, .75, 1.) if t <= end]
    return dict(orbit_id='synthetic', variant=variant, run_status=status, error=None,
                outcome='host_contact' if event_time is not None and status == 'completed'
                else 'survived' if status == 'completed' else 'unresolved',
                event=None if event_time is None else dict(event='reference_contact', time=event_time, body=0),
                last_valid_time=end, stopped=stopped,
                samples=[(t, [1., 0, 0, 0, 1, 0]) for t in times])


def test_p2_extension_stopped_is_incomplete_not_failure():
    case = dict(CASE)
    rows = [_row('D'), _row('T'), _row('B', end=.5, status='partial', stopped=True)]
    ext = p2.evaluate(rows, [case], 100., ['T', 'D', 'B'], allow_stopped=True, focus=['B'])
    assert ext['passed'] and not ext['complete'] and not ext['Q1_failures']
    assert sorted(p['pair'] for p in ext['pairs']) == ['D-B', 'T-B']
    assert all(p['truncated'] and p['common_time'] == .5 for p in ext['pairs'])
    strict = p2.evaluate(rows, [case], 100., ['T', 'D', 'B'])
    assert not strict['passed'] and strict['Q1_failures']
    assert not p2.blocking(ext) and p2.blocking(strict)


def test_p2_truncated_pair_compares_events_only_on_common_interval():
    case = dict(CASE)
    contact = _row('D', end=.8, event_time=.8)
    early_stop = _row('B', end=.5, status='partial', stopped=True)
    assert p2.compare(contact, early_stop, case)['Q2']
    late_stop = _row('B', end=.9, status='partial', stopped=True)  # прошёл .8 без события
    assert not p2.compare(contact, late_stop, case)['Q2']
    both = _row('B', end=.8, event_time=.8)
    result = p2.compare(contact, both, case)
    assert result['Q2'] and not result['truncated']


def test_p2_level_plan_separates_core_and_extension_and_orders_core_first():
    plan = p2.level_plan([1., 10., 100., 1000., 10000.], 1000., p2.FULL_YEARS, p2.FULL_B_MAX)
    assert plan[10.]['core'] == ['T', 'D', 'B'] and plan[10.]['extension'] == []
    assert plan[100.]['core'] == ['T', 'D'] and plan[100.]['extension'] == ['B']
    assert plan[10000.]['core'] == [] and plan[10000.]['extension'] == ['T', 'D']
    case = dict(CASE)
    jobs = [dict(case=case, years=10000., variant='T', core=False),
            dict(case=case, years=1., variant='D', core=True),
            dict(case=case, years=1000., variant='T', core=True)]
    ordered = p2.order_jobs(jobs)
    assert [j['years'] for j in ordered] == [1000., 1., 10000.]


def test_p2_budget_stops_on_deadline_and_stop_file(tmp_path):
    import time
    p2.Budget()()
    p2.Budget(deadline=time.time()+3600)()
    try:
        p2.Budget(deadline=time.time()-1)()
        raise AssertionError('срок не сработал')
    except TimeoutError as exc:
        assert p2.STOPPED in str(exc)
    stop = tmp_path/'STOP'
    budget = p2.Budget(stop_file=str(stop))
    budget()
    stop.write_text('x')
    budget.next_check = 0.0
    try:
        budget()
        raise AssertionError('стоп-файл не сработал')
    except TimeoutError as exc:
        assert p2.STOPPED in str(exc)
    assert p2.parse_deadline('2026-10-10T06:00:00Z') == p2.parse_deadline('2026-10-10T06:00:00+00:00')


def test_p2_read_rows_keeps_last_matching_line_and_skips_broken(tmp_path):
    import json
    path = tmp_path/'jobs.jsonl'
    first = dict(orbit_id='a', years=1., variant='D', job_fingerprint='f', run_status='partial')
    second = dict(first, run_status='completed')
    alien = dict(first, variant='T', job_fingerprint='other')
    path.write_text('\n'.join([json.dumps(first), json.dumps(second), json.dumps(alien), '{"orbit_id": "a", "ye'])
                    + '\n', encoding='utf-8')
    rows, ignored = p2.read_rows(path, {('a', 1., 'D'): 'f', ('a', 1., 'T'): 't'})
    assert rows[('a', 1., 'D')]['run_status'] == 'completed' and ignored == 2
