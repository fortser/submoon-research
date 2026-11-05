"""W2-T061 (W2-P015, Фаза 3): правила сходимости, классы К1–К4, состав блоков, сетка, исполнитель.

Без новых научных интеграций: синтетические строки; короткие проверки, что плотная сетка не меняет
численный путь D (и B в WSL), — на 0.05 года одной архивной единицы."""
import dataclasses
import math
from pathlib import Path

import numpy as np
import pytest

from submoon_research.diagnostics import convergence as cv
from test_p2_file_contracts import load

ROOT = Path(__file__).resolve().parents[2]
YEAR = cv.YEAR
THR = dict(event_time_tight_p0=1e-6, event_time_p0=1.0, contraction=0.1, sigma_num_window_years=1.0,
           sensitive_scale_floor=1e-10, k2_event_margin_p0=10.0, k3_min_directions=2, k3_time_spread_p0=10.0)
LADDERS = dict(dop853=['D', 'Ds', 'T', 'Ts', 'U'], ias15=['B', 'Bs', 'Be'])
SCALES = [1e-12, 1e-10, 1e-8]
DIRS = ['+R', '-R', '+T', '-T', '+N', '-N', 'I0', 'I1', 'I2', 'I3']


@pytest.fixture(scope='module')
def ph3():
    return load('phase3_convergence')


@pytest.fixture(scope='module')
def cfg(ph3):
    return ph3.load_config()


def row(outcome='survived', t=None, years=1000.0, reason=None, status='completed', **extra):
    event = None
    if outcome in cv.TERMINAL:
        event = dict(event='operational_escape' if outcome == 'operational_escape' else 'reference_contact', time=t)
    return dict(outcome=outcome, unresolved_reason=reason, run_status=status, event=event,
                last_valid_time=t if t is not None else years*YEAR, years=years, **extra)


# ---------------------------------------------------------------- конфигурация

def test_modes_reproduce_archive_and_change_one_factor(ph3, cfg):
    from submoon_research.dynamics.engine_compare import NumericMode
    mode = {c: ph3.numeric_mode(cfg, c)[1] for c in cfg['modes']}
    assert mode['D'] == NumericMode() and mode['B'] == NumericMode() and mode['T'] == NumericMode.tight()

    def changed(a, b):
        return {k for k, v in dataclasses.asdict(mode[a]).items() if dataclasses.asdict(mode[b])[k] != v}
    assert changed('D', 'Ds') == {'step_fraction'}
    assert changed('T', 'Ts') == {'step_fraction'}
    assert changed('Ts', 'U') == {'rtol', 'atol_position', 'atol_velocity'}
    assert changed('B', 'Bs') == {'step_fraction'} and changed('B', 'Be') == {'epsilon'}
    assert changed('Ds', 'T') >= {'rtol', 'atol_position', 'atol_velocity'} and 'step_fraction' not in changed('Ds', 'T')


def test_frozen_targets_and_deferred(cfg):
    targets = [t['orbit_id'] for t in cfg['targets']]
    assert len(targets) == 8 and 'iapetus:canonical_volume:0037' not in targets
    assert 'iapetus:canonical_volume:0037' in cfg['deferred']['avx512_starts']
    assert len(cfg['deferred']['phase2_high_e']) == 15
    assert not set(targets) & set(cfg['deferred']['phase2_high_e'])


def test_job_grid_dense_window_and_cap(ph3, cfg):
    g = cfg['grid']
    p0 = 1e6
    times, full = ph3.job_grid(1000*YEAR, p0, 1.0, g)
    assert times[0] == 0.0 and times[-1] == 1000*YEAR and np.all(np.diff(times) > 0)
    assert full.sum() == g['log_points']+1
    dense = times[(times > 0) & (times < 1.0*YEAR) & ~full]
    assert np.allclose(np.diff(dense)[:10], g['dense_step_p0']*p0)
    capped, _ = ph3.job_grid(1000*YEAR, 100.0, 50.0, g)  # маленький P0: окно ограничено числом точек
    assert len(capped) <= g['dense_max_points']+g['log_points']+1
    sparse, f = ph3.job_grid(1000*YEAR, p0, 0.0, g)
    assert len(sparse) == f.sum() == g['log_points']+1


def test_plan_jobs_blocks(ph3, cfg):
    cases = [dict(t, role='target') for t in cfg['targets']]
    cases.append(dict(orbit_id='x:y:0001', block='P1', role='control', high_e=False, codes=cfg['controls']['codes'],
                      perturb_family='Ts'))
    p0 = ph3.plan_jobs('P0', cases, cfg)
    assert len(p0) == 3*8 and {j['code'] for j in p0} == set(cfg['modes'])
    p1 = ph3.plan_jobs('P1', cases, cfg, b_years={'ganymede:canonical_volume:0126': 400.0})
    assert len(p1) == 4*8+5+6
    assert {j['years'] for j in p1 if j['orbit_id'] == 'ganymede:canonical_volume:0126' and j['code'][0] == 'B'} == {400.0}
    assert {j['years'] for j in p1 if j['orbit_id'] == 'ganymede:canonical_volume:0126' and j['code'][0] != 'B'} == {1000.0}
    pert = ph3.plan_jobs('perturb', cases, cfg)
    assert len(pert) == 9*30 and all(j['dense_window_years'] == 0.0 for j in pert)
    assert {j['code'] for j in pert if j['orbit_id'].startswith('himalia:canonical_volume:0013')} == {'B'}
    with pytest.raises(ValueError):
        ph3.plan_jobs('P9', cases, cfg)


def test_job_fingerprint_depends_on_everything(ph3, cfg):
    unit = dict(orbit_id='a', state=[0.0]*12, gms=[1.0], figures=[], radii=[1.0], period_seconds=1.0)
    spec = dict(orbit_id='a', code='D', years=1000.0, perturbation=None, dense_window_years=50.0)
    impl = dict(dop853='x', ias15='y')
    base = ph3.job_fingerprint(spec, unit, cfg, impl)
    assert base != ph3.job_fingerprint(dict(spec, code='Ds'), unit, cfg, impl)
    assert base != ph3.job_fingerprint(dict(spec, perturbation=dict(direction='+R', scale=1e-12, seed=1)), unit, cfg, impl)
    assert base != ph3.job_fingerprint(spec, unit, cfg, dict(impl, dop853='z'))
    assert base == ph3.job_fingerprint(spec, unit, cfg, dict(impl, ias15='other'))


def test_check_unit_exact(ph3):
    unit = dict(state=[0.0]*6+[1., 2., 3., 4., 5., 6.])
    rows = [dict(years=1.0, variant='D', job_fingerprint='fp', samples=[[0.0, [1., 2., 3., 4., 5., 6.]]]),
            dict(years=1.0, variant='T', job_fingerprint='fp', samples=[[0.0, [1., 2., 3., 4., 5., 7.]]])]
    bad = ph3.check_unit(unit, rows, {}, lambda u, y, v, i: 'fp')
    assert bad == [dict(years=1.0, variant='T', job_fingerprint=True, initial_probe=False)]


def test_b_fallback_and_scheduling(ph3):
    cases = [dict(orbit_id='g:c:0126', b_horizon_fallback_years=400.0), dict(orbit_id='h:c:0012')]
    assert ph3.b_fallback(cases, dict(predicted_full_horizon_seconds={'g:c:0126|B': 3*3600.})) == {'g:c:0126': 400.0}
    assert ph3.b_fallback(cases, dict(predicted_full_horizon_seconds={'g:c:0126|B': 3600.})) == {}
    assert ph3.b_fallback(cases, None) == {}
    assert ph3.lpt_wall([5, 4, 3, 3], 2) == 8 and ph3.lpt_wall([5, 1], 4) == 5


def test_predict_seconds(ph3, cfg):
    case = dict(context=dict(p2_2a_D=dict(time_years=100.0), p2_2a_T=dict(time_years=500.0)))
    r = dict(code='D', run_status='completed', outcome='survived', event=None, wall=2.0, years=10.0)
    assert ph3.predict_seconds(r, case, cfg) == pytest.approx(20.0)
    assert ph3.predict_seconds(dict(r, code='Ts'), case, cfg) == pytest.approx(100.0)
    assert ph3.predict_seconds(dict(r, code='B'), case, cfg) == pytest.approx(100.0)  # B: нет B-архива -> T
    assert ph3.predict_seconds(dict(r, event=dict(time=1.0)), case, cfg) == 2.0


# ---------------------------------------------------------------- метрики

def test_labels_and_times():
    assert cv.label(row()) == 'survived' and cv.outcome_time(row()) == 1000*YEAR
    assert cv.label(row('unresolved', 5.0, reason='high_e_prolonged')) == 'unresolved:high_e_prolonged'
    assert cv.label(row(status='partial')) == 'incomplete:partial'
    assert cv.outcome_time(row('host_contact', 7.0)) == 7.0
    assert cv.differs(row('host_contact', 7.0), row('host_contact', 8.5), p0=1.0)
    assert not cv.differs(row('host_contact', 7.0), row('host_contact', 7.5), p0=1.0)


def test_ladder_status():
    esc = lambda t: row('operational_escape', t)  # noqa: E731
    conv = cv.ladder_status(dict(D=esc(100.), Ds=esc(90.), T=esc(80.), Ts=esc(80.5), U=esc(80.52)),
                            LADDERS['dop853'], 1.0, THR)
    assert conv['status'] == 'converged' and conv['finest'] == ['Ts', 'U'] and conv['label'] == 'operational_escape'
    slow = cv.ladder_status(dict(T=esc(80.), Ts=esc(80.5), U=esc(80.9)), LADDERS['dop853'], 1.0, THR)
    assert slow['status'] == 'not_converged'  # нет сжатия 0.4 > 0.1·0.5
    tight = cv.ladder_status(dict(T=esc(80.), Ts=esc(80.), U=esc(80.0+1e-9)), LADDERS['dop853'], 1.0, THR)
    assert tight['status'] == 'converged'
    flip = cv.ladder_status(dict(T=esc(500.), Ts=row(), U=row()), LADDERS['dop853'], 1.0, THR)
    assert flip['status'] == 'converged'  # предыдущая пара с разными метками — сжатие выполнено
    assert cv.ladder_status(dict(T=row(), U=esc(5.)), LADDERS['dop853'], 1.0, THR)['status'] == 'not_converged'
    hi = cv.ladder_status(dict(Ts=row(), U=row('unresolved', 5., reason='high_e_prolonged')), LADDERS['dop853'], 1., THR)
    assert hi['status'] == 'unresolved'
    assert cv.ladder_status(dict(B=row()), LADDERS['ias15'], 1.0, THR)['status'] == 'insufficient'


def test_divergence_and_tau():
    ta = np.array([0., 1., 2., 3.])
    pa = np.zeros((4, 6))
    pb = np.zeros((3, 6))
    pb[:, 0] = [1e-10, 1e-5, 1e-2]
    t, d = cv.divergence(ta, pa, np.array([1., 2., 3.]), pb, a0=1.0, n0=1.0)
    assert list(t) == [1., 2., 3.] and np.allclose(d, [1e-10, 1e-5, 1e-2])
    tau = cv.first_exceedance(t, d, [1e-9, 1e-6, 1e-3, 1e-1])
    assert tau == {'1e-09': 2.0, '1e-06': 2.0, '0.001': 3.0, '0.1': None}


def test_growth_fit_distinguishes_exponential_and_power():
    t = np.linspace(1, 50, 60)
    exp = cv.growth_fit(t, 1e-12*np.exp(0.3*t))
    pw = cv.growth_fit(t, 1e-12*t**2)
    assert exp['exponential']['r2'] > exp['power']['r2'] and pw['power']['r2'] > pw['exponential']['r2']
    assert pw['power']['slope'] == pytest.approx(2.0)
    assert cv.growth_fit(t[:3], t[:3]*1e-10)['exponential'] is None


def test_perturbation_vectors():
    probe = [1000.0, 0.0, 0.0, 0.0, 2.0, 0.0]
    for d, axis in (('+R', 0), ('+T', 1), ('+N', 2)):
        out = np.array(cv.perturbation(probe, 500.0, 1e-3, d, 1e-8, 1))-np.array(probe)
        assert out[axis] == pytest.approx(500.0*1e-8) and np.count_nonzero(out) == 1
    minus = np.array(cv.perturbation(probe, 500.0, 1e-3, '-T', 1e-8, 1))-np.array(probe)
    assert minus[1] == pytest.approx(-5e-6)
    iso = np.array(cv.perturbation(probe, 500.0, 1e-3, 'I2', 1e-10, 7))-np.array(probe)
    scaled = np.concatenate([iso[:3]/500.0, iso[3:]/(1e-3*500.0)])
    assert np.linalg.norm(scaled) == pytest.approx(1e-10)
    assert np.allclose(cv.isotropic_directions(7, 4)[2], cv.isotropic_directions(7, 3)[2])
    with pytest.raises(ValueError):
        cv.perturbation(probe, 1., 1., 'Q', 1., 1)


def test_grid_diagnostics_circular_orbit():
    gm, r = 1.0, 1.0
    t = np.linspace(0, 2*math.pi, 50)
    probe = np.column_stack([np.cos(t), np.sin(t), 0*t, -np.sin(t), np.cos(t), 0*t])
    d = cv.grid_diagnostics(t, probe, gm, hill_km=2.0)
    assert d['min_distance_km'] == pytest.approx(r) and d['rh_crossings'] == 0
    assert d['max_bound_e'] < 1e-12 and d['first_bound_e_at_limit_time'] is None


# ---------------------------------------------------------------- классы и правило режима

def dense_pair(n=400, t_max=1000*YEAR, growth=None):
    t = np.linspace(0, t_max, n)
    a = np.zeros((n, 6))
    b = np.zeros((n, 6))
    b[:, 0] = growth(t) if growth else 0.0
    return (t, a), (t, b)


def case(rows, perturb=None, base='Ts', high_e=False, dense=None):
    dense = dense or {}
    return dict(rows=rows, dense=dense, ladders=LADDERS, a0=1.0, n0=1.0, p0=1.0, high_e=high_e,
                perturb_family=base, perturb_rows=perturb or [], scales=SCALES)


def perturbed(base_row, flips_at=(), dt=0.0):
    out = []
    for s in SCALES:
        for k, d in enumerate(DIRS):
            r = dict(base_row, perturbation=dict(direction=d, scale=s))
            if s in flips_at and k < 3:
                r = dict(row('host_contact', 3.0), perturbation=dict(direction=d, scale=s))
            elif dt and r.get('event'):
                r['event'] = dict(r['event'], time=r['event']['time']+dt*(k+1))
            out.append(r)
    return out


def test_classify_k1_d_wrong():
    surv = row()
    rows = dict(D=row('operational_escape', 600*YEAR), Ds=surv, T=surv, Ts=surv, U=surv, B=surv, Bs=surv, Be=surv)
    a, b = dense_pair(n=2000, t_max=2*YEAR)
    res = cv.classify(case(rows, perturbed(surv), dense=dict(Ts=a, U=b)), THR)
    assert res['cls'] == 'K1' and res['confidence'] == 'high' and res['wrong'] == ['D']
    assert res['sigma_num'] == 0.0 and res['s_star'] == 1e-10


def test_classify_k1_medium_without_ias15():
    surv = row()
    rows = dict(D=row('operational_escape', 600*YEAR), Ds=surv, T=surv, Ts=surv, U=surv)
    res = cv.classify(case(rows, perturbed(surv)), THR)
    assert res['cls'] == 'K1' and res['confidence'] == 'medium'


def test_classify_k2_event_resolution():
    rows = dict(D=row('operational_escape', 1.002*YEAR), Ds=row('host_contact', 1.037*YEAR),
                T=row('host_contact', 1.037*YEAR), Ts=row('host_contact', 1.037*YEAR), U=row('host_contact', 1.037*YEAR))
    a, b = dense_pair(n=2000, t_max=1.002*YEAR)
    res = cv.classify(case(rows, dense=dict(D=a, U=b)), THR)
    assert res['cls'] == 'K2' and res['k2'][0]['covered']
    early = dense_pair(n=2000, t_max=1.002*YEAR, growth=lambda t: np.where(t > 0.1*YEAR, 1e-3, 0.0))
    assert cv.classify(case(rows, dense=dict(D=early[0], U=early[1])), THR)['cls'] != 'K2'


def test_classify_k0_not_reproduced():
    surv = row()
    rows = dict(D=surv, Ds=surv, T=surv, Ts=surv, U=surv, B=surv, Bs=surv, Be=surv)
    res = cv.classify(case(rows, perturbed(surv)), THR)
    assert res['cls'] == 'K0'
    rows['D'] = row('host_contact', 500*YEAR)
    assert cv.classify(case(rows, perturbed(surv)), THR)['cls'] == 'K1'


def test_classify_k3_and_k4():
    esc = lambda t: row('operational_escape', t)  # noqa: E731
    rows = dict(D=esc(95*YEAR), Ds=esc(60*YEAR), T=esc(77*YEAR), Ts=esc(50*YEAR), U=esc(40*YEAR),
                B=esc(38*YEAR), Bs=esc(70*YEAR), Be=esc(45*YEAR))
    res = cv.classify(case(rows, perturbed(rows['Ts'], dt=20.0)), THR)
    assert res['cls'] == 'K3' and res['confidence'] == 'high'
    res = cv.classify(case(rows), THR)  # без возмущений ни К1, ни К3
    assert res['cls'] == 'K4'


def test_classify_high_e_uses_ias15():
    surv = row()
    hi = row('unresolved', 11*YEAR, reason='high_e_prolonged')
    rows = dict(D=hi, Ds=hi, T=hi, Ts=hi, U=hi, B=row('operational_escape', 119*YEAR), Bs=surv, Be=surv)
    res = cv.classify(case(rows, perturbed(surv), base='Be', high_e=True), THR)
    assert res['coarse'] == 'B' and res['cls'] == 'K1' and res['confidence'] == 'medium'


def test_decide_regime_rules():
    t = lambda o, cls, wrong=None, high_e=False: dict(orbit_id=o, cls=cls, wrong=wrong, high_e=high_e)  # noqa: E731
    r = cv.decide_regime([t('a', 'K1', ['D', 'Ds']), t('b', 'K3')], [], [])
    assert r['verdict']['rule'] == 1 and r['verdict']['candidate'] == 'T'
    r = cv.decide_regime([t('a', 'K1', ['D']), t('b', 'K1', ['D', 'T'])], [], [])
    assert r['verdict']['rule'] == 2
    r = cv.decide_regime([t('a', 'K2'), t('b', 'K3'), t('c', 'K1', ['B'], high_e=True)], [], [])
    assert r['verdict']['rule'] == 3
    r = cv.decide_regime([t('a', 'K0'), t('b', 'K3')], [], [])
    assert r['verdict']['rule'] == 3 and r['verdict']['not_reproduced'] == ['a']
    assert cv.decide_regime([t('a', 'K0')], [], [])['verdict']['rule'] is None
    r = cv.decide_regime([t('a', 'K4'), t('b', 'K3')], [dict(orbit_id='c', labels=dict(D='survived', T='host_contact'))],
                         [dict(orbit_id='h', labels=dict(B='survived', Bs='survived', Be='operational_escape'))])
    assert r['verdict']['rule'] is None and {f['rule'] for f in r['flags']} == {4, 5}


def test_select_controls_rules():
    P = 10.0

    def br(unit, host, outcome, t=None, wall=10.0):
        r = row(outcome, t)
        r.update(unit=unit, host=host, wall=wall)
        return r
    b = {'h:a:0001': br(1, 'h', 'host_contact', 100.), 'h:a:0002': br(2, 'h', 'host_contact', 500.),
         'h:a:0003': br(3, 'h', 'host_contact', 900., wall=1000.), 'h:a:0004': br(4, 'h', 'survived', wall=50.),
         'h:a:0005': br(5, 'h', 'survived', wall=20.), 'h:a:0006': br(6, 'h', 'operational_escape', 50.),
         'h:a:0007': br(7, 'h', 'host_contact', 800.)}
    d = {k: dict(v) for k, v in b.items()}
    t = {k: dict(v) for k, v in b.items()}
    t['h:a:0006'] = row('operational_escape', 50.+2*P)  # время расходится больше 1 P0
    d['h:a:0007'] = row('survived')  # метка расходится
    out = cv.select_controls(b, d, t, {k: P for k in b}, excluded={'h:a:0001'}, cost_cap=300.)
    assert out['chosen'] == {'h:host_contact': 'h:a:0002', 'h:survived': 'h:a:0005'}
    assert out['candidates'] == {'h:host_contact': 1, 'h:survived': 2}
    assert cv.perturbed_controls(out['chosen'], b) == {'host_contact': 'h:a:0002', 'survived': 'h:a:0005'}


def test_freeze_on_archives(ph3, cfg, tmp_path):
    if not (ROOT/cfg['archives']['p2_2a']['run']/'jobs.jsonl').exists():
        pytest.skip('архивы P2-2a/B144 отсутствуют')
    doc = ph3.freeze(cfg, out=tmp_path/'cases.json')
    targets = [c for c in doc['cases'] if c['role'] == 'target']
    controls = [c for c in doc['cases'] if c['role'] == 'control']
    assert len(targets) == 8 and 1 <= len(controls) <= 9
    assert not {c['orbit_id'] for c in controls} & {c['orbit_id'] for c in targets}
    assert sum(1 for c in controls if c['perturb_family']) == len(doc['controls']['perturbed'])
    assert {c['source'] for c in targets if c['orbit_id'].split(':')[2] in ('0097', '0086', '0126')} == {'regenerate'}
    frozen = ROOT/'data/manifests/W2_phase3_cases_v1.json'
    if frozen.exists():  # тот же выбор, что зафиксирован в журнале
        from submoon_research.contracts.p2_files import read_json
        assert read_json(frozen)['controls']['chosen'] == doc['controls']['chosen']


# ---------------------------------------------------------------- численный путь не зависит от сетки

def _unit():
    path = ROOT/'runs/W2-B144-57496493/units.json'
    if not path.exists():
        pytest.skip('units.json B144 отсутствует')
    from submoon_research.contracts.p2_files import read_json
    return next(u for u in read_json(path)['units'] if u['orbit_id'] == 'himalia:canonical_volume:0013')


@pytest.mark.parametrize('family', ['dop853', 'ias15'])
def test_dense_grid_does_not_change_numerics(ph3, cfg, family):
    if family == 'ias15':
        pytest.importorskip('reboundx')
    from submoon_research.dynamics.compiled_dop853 import integrate_compiled
    from submoon_research.dynamics.engine_compare import integrate_engine
    unit = _unit()
    code = 'D' if family == 'dop853' else 'B'
    mode = ph3.numeric_mode(cfg, code)[1]
    horizon = 0.05*YEAR
    p0 = unit['period_seconds']
    outs = []
    for window in (0.0, 1.0):
        times, _ = ph3.job_grid(horizon, p0, window, cfg['grid'])
        kw = dict(period=p0, mode=mode, hill_km=unit['hill_km'], escape_window=unit['escape_window'], times=times)
        if family == 'ias15':
            outs.append(integrate_engine('ias15_reboundx', unit['state'], unit['gms'], unit['figures'], unit['radii'],
                                         horizon, **kw))
        else:
            outs.append(integrate_compiled(unit['state'], unit['gms'], unit['figures'], unit['radii'], horizon, **kw))
    sparse, dense = outs
    assert len(dense['samples']) > len(sparse['samples'])
    assert dense['steps'] == sparse['steps'] and dense['final_state'] == sparse['final_state']
    assert dense['event'] == sparse['event'] and dense['last_valid_time'] == sparse['last_valid_time']
