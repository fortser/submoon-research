"""W2-P006 (P2-2): выборка единиц, состав заданий и точные границы разбора §3 на синтетике."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
YEAR = 365.25*86400.0


def load(name):
    sys.path.insert(0, str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_units_reproducible_and_match_plan_sample():
    s = load('admission_p2_2')
    plan = load('plan_p2_2')
    first, again = s.units(2), s.units(2)
    assert first == again
    assert len(first) == 18 and len({u['orbit_id'] for u in first}) == 18
    for u, p in zip(first, plan.sample(2)):
        assert (u['host'], u['measure'], u['unit'], u['seed']) == (p['host'], p['measure'], p['unit'], p['seed'])
        assert u['seed'] == s.SEED_BASE + 9*u['unit'] + u['cell']
        assert (u['a_km'], u['eccentricity'], u['cos_i'], u['period_seconds']) == (p['a_km'], p['e'], p['cos_i'], p['period_s'])
    # Состояние: массивные тела хозяина как в load_cases, пробное — запись генератора W1.
    prepared = s.setups()
    for u in first:
        state = np.asarray(u['state']).reshape(-1, 6)
        assert np.array_equal(state[:-1], prepared[u['host']]['massive'])
        record = s.generate(prepared[u['host']]['domain'], u['measure'], power=0, seed=u['seed'],
                            randomization=u['unit'], basis=prepared[u['host']]['basis'])['records'][0]
        assert state[-1].tolist() == record['position']+record['velocity']
    # Продолжение потока (P2-2b): j >= unit_start даёт те же единицы.
    assert s.units(1, unit_start=1) == first[9:]


def test_plan_keys_and_params():
    s = load('admission_p2_2')
    unit_list = s.units(3)
    params = s.normalize_params(3, 1, 0, [10, 1], [10])
    planned = s.plan_keys(unit_list, params)
    assert sorted(planned) == [1.0, 10.0]
    assert len(planned[1.0]) == 27*2 and len(planned[10.0]) == 27*2 + 9
    assert {k[0].rsplit(':', 1)[1] for k in planned[10.0] if k[2] == 'B'} == {'0000'}
    for bad in ((0, 0, 0, [1], []), (3, 4, 0, [1], []), (3, 1, 0, [1], [10]), (3, 1, 0, [0], [])):
        with pytest.raises(ValueError):
            s.normalize_params(*bad)
    full = s.normalize_params(**{k: v for k, v in s.FULL.items()})
    assert s.is_full_scope(full) and not s.is_full_scope(params)
    total = sum(len(k) for k in s.plan_keys([dict(orbit_id=str(i), unit=i//9) for i in range(1152)], full).values())
    assert total == 1152*2*4 + 144*2


def test_clopper_pearson_exact():
    s = load('admission_p2_2')
    from scipy.stats import binom
    for n in (10, 598, 1152):
        assert s.cp_upper(0, n, 0.05) == pytest.approx(1-0.05**(1/n), rel=1e-12)
    # Минимальный объём плана для 0 расхождений (таблица §3): 598 при одной страте.
    assert s.cp_upper(0, 598, 0.05) <= 0.005 < s.cp_upper(0, 597, 0.05)
    for x, n in ((1, 949), (3, 1000), (5, 2108)):
        upper = s.cp_upper(x, n, 0.05)
        assert binom.cdf(x, n, upper) == pytest.approx(0.05, rel=1e-9)
        lo, hi = s.cp_interval(x, n, 0.05)
        assert binom.sf(x-1, n, lo) == pytest.approx(0.025, rel=1e-9)
        assert binom.cdf(x, n, hi) == pytest.approx(0.025, rel=1e-9)
    assert s.cp_upper(4, 4, 0.05) == 1.0 and s.cp_interval(0, 4, 0.05)[0] == 0.0


def test_newcombe_paired_properties_and_coverage():
    s = load('admission_p2_2')
    d, lo, hi = s.newcombe_paired(300, 12, 4, 836, 0.01)
    assert lo < d < hi and d == pytest.approx(8/1152)
    d2, lo2, hi2 = s.newcombe_paired(300, 4, 12, 836, 0.01)
    assert max(abs(d2+d), abs(lo2+hi), abs(hi2+lo)) < 1e-15
    # Нет расхождений и большая выборка — интервал внутри ±0.005; одностороннее смещение 40 — вне.
    _, lo, hi = s.newcombe_paired(400, 0, 0, 752, 0.01)
    assert -0.005 < lo <= 0 <= hi < 0.005
    _, lo, _ = s.newcombe_paired(380, 40, 0, 732, 0.01)
    assert lo > 0.005
    # Покрытие 95%-интервала на парных бернуллиевских данных (Монте-Карло).
    rng = np.random.default_rng(20261012)
    probs = np.array([0.30, 0.02, 0.01, 0.67])
    truth = probs[1]-probs[2]
    covered = 0
    for counts in rng.multinomial(300, probs, size=2000):
        _, lo, hi = s.newcombe_paired(*map(int, counts), 0.05)
        covered += lo <= truth <= hi
    assert covered/2000 >= 0.93


def synthetic(n_per_cell=128, years=(1000.0,), b_per_cell=0, b_years=()):
    s = load('admission_p2_2')
    unit_list = []
    for j in range(n_per_cell):
        for c, (host, measure) in enumerate(s.CELLS):
            unit_list.append(dict(orbit_id=f'{host}:{measure}:{j:04d}', host=host, measure=measure, cell=c,
                                  unit=j, seed=c, a_km=1e4, period_seconds=86400.0))
    params = s.normalize_params(n_per_cell, b_per_cell, 0, years, b_years)
    return s, unit_list, params


def make_row(unit, years, variant, outcome='survived', reason=None, status='completed', event=None):
    end = years*YEAR if event is None else event['time']
    return dict(orbit_id=unit['orbit_id'], host=unit['host'], years=float(years), variant=variant,
                run_status=status, error=None if status == 'completed' else 'x', outcome=outcome,
                unresolved_reason=reason, event=event, last_valid_time=end if status == 'completed' else 0.0,
                samples=[(0.0, [1e4, 0, 0, 0, 1, 0])], steps=100, wall=0.01)


def rows_for(s, unit_list, params, change=lambda unit, years, variant: {}):
    planned = s.plan_keys(unit_list, params)
    by_id = {u['orbit_id']: u for u in unit_list}
    return [make_row(by_id[o], y, v, **change(by_id[o], y, v)) for keys in planned.values() for o, y, v in keys]


def test_analyze_equivalent_inconclusive_not_equivalent():
    s, unit_list, params = synthetic()
    rows = rows_for(s, unit_list, params)
    analysis = s.analyze(rows, unit_list, params, full_scope=True)
    pooled = analysis['horizons']['1000']['pairs']['D-T']['levels']['pooled'][0]
    assert (pooled['n'], pooled['discordant'], pooled['verdict']) == (1152, 0, 'equivalent')
    assert pooled['cp_upper'] == pytest.approx(1-0.05**(1/1152))
    assert analysis['decision']['verdict'] == 'equivalent'
    hosts = analysis['horizons']['1000']['pairs']['D-T']['levels']['hosts']
    cells = analysis['horizons']['1000']['pairs']['D-T']['levels']['cells']
    assert len(hosts) == 3 and len(cells) == 9
    assert hosts[0]['alpha_cp'] == pytest.approx(0.05/3) and cells[0]['alpha_newcombe'] == pytest.approx(0.05/45)
    assert {h['verdict'] for h in hosts} == {'inconclusive'}  # 384 единиц: граница 0.0106 > 0.005
    assert s.analyze(rows, unit_list, params, full_scope=False)['decision']['verdict'] == 'not_decided'

    # 5 расхождений в разные стороны — мощности не хватает: «не определено».
    flips = {u['orbit_id'] for u in unit_list[:5]}
    rows = rows_for(s, unit_list, params, lambda u, y, v: dict(outcome='host_contact', event=dict(
        event='reference_contact', time=0.5*YEAR, body=0)) if v == 'T' and u['orbit_id'] in flips else {})
    result = s.analyze(rows, unit_list, params, full_scope=True)
    pooled = result['horizons']['1000']['pairs']['D-T']['levels']['pooled'][0]
    assert pooled['discordant'] == 5 and pooled['verdict'] == 'inconclusive'
    assert result['horizons']['1000']['pairs']['D-T']['transitions'] == {'survived -> host_contact': 5}

    # 60 односторонних расхождений (Δ = 0.052) — «не эквивалентно».
    flips = {u['orbit_id'] for u in unit_list[:60]}
    rows = rows_for(s, unit_list, params, lambda u, y, v: dict(outcome='host_contact', event=dict(
        event='reference_contact', time=0.5*YEAR, body=0)) if v == 'T' and u['orbit_id'] in flips else {})
    result = s.analyze(rows, unit_list, params, full_scope=True)
    assert result['decision']['verdict'] == 'not_equivalent'
    category = result['horizons']['1000']['pairs']['D-T']['levels']['pooled'][0]['categories']['survived']
    assert category['newcombe'][0] > 0.005 and category['delta'] == pytest.approx(60/1152)


def test_unresolved_reason_is_a_separate_label():
    s, unit_list, params = synthetic(n_per_cell=4)
    first = unit_list[0]['orbit_id']
    rows = rows_for(s, unit_list, params, lambda u, y, v: dict(
        outcome='unresolved', reason='high_e_prolonged' if v == 'D' else 'escape_not_resolved_at_horizon')
        if u['orbit_id'] == first else {})
    pooled = s.analyze(rows, unit_list, params, True)['horizons']['1000']['pairs']['D-T']['levels']['pooled'][0]
    assert pooled['discordant'] == 1
    assert pooled['categories']['unresolved']['delta'] == 0.0  # категория совпала, канал потерь — нет


def test_incomplete_or_failed_final_horizon_is_not_decided():
    s, unit_list, params = synthetic(n_per_cell=128, years=(100.0, 1000.0))
    first = unit_list[0]['orbit_id']
    stopped = rows_for(s, unit_list, params, lambda u, y, v: dict(status='partial', outcome='unresolved',
                       reason='computational') if u['orbit_id'] == first and y == 1000.0 and v == 'T' else {})
    result = s.analyze(stopped, unit_list, params, True)
    assert not result['complete'] and result['horizons']['100']['complete']
    assert result['decision']['verdict'] == 'not_decided'
    assert result['horizons']['1000']['pairs']['D-T']['units'] == 1151
    failed = rows_for(s, unit_list, params, lambda u, y, v: dict(status='failed', outcome='unresolved',
                      reason='computational') if u['orbit_id'] == first and y == 1000.0 and v == 'D' else {})
    result = s.analyze(failed, unit_list, params, True)
    assert result['complete'] and result['decision']['verdict'] == 'not_decided'
    assert any('отказы' in r for r in result['decision']['reasons'])
    # 1 расхождение из 1152 само по себе дало бы «эквивалентно» — решение блокирует отказ.
    assert result['decision']['stratum_verdict'] == 'equivalent'
    missing = [r for r in rows_for(s, unit_list, params) if not (r['orbit_id'] == first and r['variant'] == 'D')]
    assert s.analyze(missing, unit_list, params, True)['decision']['verdict'] == 'not_decided'


def test_stopped_or_missing_b_does_not_block_decision():
    """W2-D026: остановленные сроком или недостающие B на финальном горизонте не блокируют решение D–T."""
    s, unit_list, params = synthetic(n_per_cell=128, years=(100.0, 1000.0), b_per_cell=16, b_years=(100.0, 1000.0))
    slow = {u['orbit_id'] for u in unit_list[:3]}
    rows = rows_for(s, unit_list, params, lambda u, y, v: dict(status='partial', outcome='unresolved',
                    reason='computational') if v == 'B' and y == 1000.0 and u['orbit_id'] in slow else {})
    result = s.analyze(rows, unit_list, params, True)
    final = result['horizons']['1000']
    assert not result['complete'] and not final['complete'] and final['dt_complete']
    assert (final['b_planned'], final['b_unfinished']) == (144, 3)
    assert result['decision']['verdict'] == 'equivalent' and result['decision']['reasons'] == []
    assert result['decision']['b_unfinished'] == 3 and result['decision']['rule'] == 'W2-D026'
    assert any('B не завершено' in n for n in result['decision']['notes'])
    assert final['pairs']['D-B']['units'] == 141  # пары с B — только по завершённым
    assert result['analysis_version'] == s.ANALYSIS_VERSION
    missing = [r for r in rows_for(s, unit_list, params)
               if not (r['variant'] == 'B' and r['years'] == 1000.0 and r['orbit_id'] in slow)]
    result = s.analyze(missing, unit_list, params, True)
    assert result['decision']['verdict'] == 'equivalent' and result['horizons']['1000']['b_unfinished'] == 3
    # Отказ B не блокирует (как прежде), отказ D/T — блокирует.
    b_failed = rows_for(s, unit_list, params, lambda u, y, v: dict(status='failed', outcome='unresolved',
                        reason='computational') if v == 'B' and y == 1000.0 and u['orbit_id'] in slow else {})
    assert s.analyze(b_failed, unit_list, params, True)['decision']['verdict'] == 'equivalent'
    # Остановленный D на финальном горизонте по-прежнему блокирует.
    first = unit_list[0]['orbit_id']
    d_stopped = rows_for(s, unit_list, params, lambda u, y, v: dict(status='partial', outcome='unresolved',
                         reason='computational') if v == 'D' and y == 1000.0 and u['orbit_id'] == first else {})
    result = s.analyze(d_stopped, unit_list, params, True)
    assert result['decision']['verdict'] == 'not_decided' and not result['horizons']['1000']['dt_complete']


def test_b_pairs_are_descriptive_with_high_e_exclusion():
    s, unit_list, params = synthetic(n_per_cell=4, years=(1000.0,), b_per_cell=2, b_years=(1000.0,))
    first = unit_list[0]['orbit_id']
    rows = rows_for(s, unit_list, params, lambda u, y, v: dict(outcome='unresolved', reason='high_e_prolonged')
                    if u['orbit_id'] == first and v in ('D', 'T') else {})
    pairs = s.analyze(rows, unit_list, params, True)['horizons']['1000']['pairs']
    assert pairs['D-T']['units'] == 36 and pairs['D-B']['units'] == 18
    db = pairs['D-B']
    assert 'verdict' not in db['levels']['pooled'][0] and db['levels']['pooled'][0]['discordant'] == 1
    assert db['excluding_high_e'] == dict(n=17, discordant=0, cp_two_sided=s.cp_interval(0, 17, 0.05))


def test_divergence_time_on_grid():
    s, unit_list, _ = synthetic(n_per_cell=1)
    unit = unit_list[0]
    a = dict(samples=[(0.0, [1e4, 0, 0, 0, 1, 0]), (10.0, [1e4, 0, 0, 0, 1, 0]), (20.0, [1e4, 0, 0, 0, 1, 0])])
    b = dict(samples=[(0.0, [1e4, 0, 0, 0, 1, 0]), (10.0, [1e4+1e-3, 0, 0, 0, 1, 0]), (20.0, [1e4+1, 0, 0, 0, 1, 0])])
    assert s.divergence_time(unit, a, b) == 20.0  # 1e-7 < порога, 1e-4 > порога
    assert s.divergence_time(unit, a, a) is None
    assert s.label(dict(outcome='unresolved', unresolved_reason=None)) == 'unresolved:unspecified'
