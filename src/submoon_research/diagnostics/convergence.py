"""W2-P015 (Фаза 3): метрики адресной сходимости, возмущения старта, классы К1–К4, правило режима.

Чистые функции без интеграций и без SciPy: их вызывают scripts/phase3_convergence.py (freeze,
analyze) и тесты. Пороги приходят из configs/experiments/W2_phase3_convergence_v1.yaml; сами
правила зафиксированы здесь до первого нового исхода (W2-D046) — изменение = новая версия.

Строка задания (row) — словарь с ключами outcome, unresolved_reason, run_status, event
({'event','time',...} или None), last_valid_time, years. Времена — секунды, P0 — период пробы.
"""
from __future__ import annotations

import math

import numpy as np

YEAR = 365.25*86400.0
DIRECTIONS_NAMED = ('+R', '-R', '+T', '-T', '+N', '-N')
TERMINAL = ('host_contact', 'other_contact', 'operational_escape')
RULES_VERSION = 'W2-PH3-rules-v1'


# ---------------------------------------------------------------- метки и времена

def label(row):
    """Метка исхода: категория; unresolved — с причиной; незавершённое задание — 'incomplete:<status>'."""
    if row.get('run_status') not in (None, 'completed'):
        return 'incomplete:'+str(row.get('run_status'))
    if row['outcome'] != 'unresolved':
        return row['outcome']
    return 'unresolved:'+(row.get('unresolved_reason') or 'unspecified')


def outcome_time(row):
    """Время исхода: момент события; для survived — горизонт; иначе — last_valid_time."""
    if row.get('event'):
        return float(row['event']['time'])
    if row['outcome'] == 'survived':
        return float(row['years'])*YEAR
    return float(row['last_valid_time'])


def determined(row):
    return label(row) in ('survived', *TERMINAL)


def differs(row_a, row_b, p0, tol_p0=1.0):
    """Разные метки или (при одинаковой) |Δt| > tol_p0·P0."""
    if label(row_a) != label(row_b):
        return True
    return abs(outcome_time(row_a)-outcome_time(row_b)) > tol_p0*p0


# ---------------------------------------------------------------- лестница и семейства

def ladder_status(rows, codes, p0, thr):
    """Сходимость семейства по двум самым точным доступным ступеням (порядок codes — от грубой к точной).

    converged: одна метка и |Δt| ≤ event_time_p0·P0, и сжатие |Δt| ≤ contraction·|Δt_prev|
    (или |Δt| ≤ event_time_tight_p0·P0; при разных метках предыдущей пары сжатие выполнено).
    unresolved: самая точная ступень не даёт определённого исхода (high-e/ошибка).
    """
    present = [c for c in codes if c in rows]
    out = dict(codes=present, status='insufficient', label=None, time=None, finest=None,
               dt_p0=None, dt_prev_p0=None)
    if len(present) < 2:
        return out
    c1, c2 = present[-2], present[-1]
    r1, r2 = rows[c1], rows[c2]
    out.update(finest=[c1, c2], label=label(r2), time=outcome_time(r2))
    if not determined(r2):
        out['status'] = 'unresolved'
        return out
    if label(r1) != label(r2):
        out['status'] = 'not_converged'
        return out
    dt = abs(outcome_time(r1)-outcome_time(r2))/p0
    out['dt_p0'] = dt
    contraction_ok = True
    if len(present) >= 3:
        r0 = rows[present[-3]]
        if label(r0) == label(r1):
            prev = abs(outcome_time(r0)-outcome_time(r1))/p0
            out['dt_prev_p0'] = prev
            contraction_ok = dt <= thr['contraction']*prev
    tight = dt <= thr['event_time_tight_p0']
    out['status'] = 'converged' if dt <= thr['event_time_p0'] and (contraction_ok or tight) else 'not_converged'
    return out


def families_agree(a, b, p0, thr):
    if a['status'] != 'converged' or b['status'] != 'converged':
        return None
    return a['label'] == b['label'] and abs(a['time']-b['time']) <= thr['event_time_p0']*p0


# ---------------------------------------------------------------- траектории

def divergence(times_a, probe_a, times_b, probe_b, a0, n0):
    """Δ(t) = max(|Δr|/a0, |Δv|/(n0·a0)) на точно общих метках времени (без интерполяции)."""
    ta, tb = np.asarray(times_a, float), np.asarray(times_b, float)
    common, ia, ib = np.intersect1d(ta, tb, return_indices=True)
    pa, pb = np.asarray(probe_a, float)[ia], np.asarray(probe_b, float)[ib]
    dr = np.linalg.norm(pa[:, :3]-pb[:, :3], axis=1)/a0
    dv = np.linalg.norm(pa[:, 3:6]-pb[:, 3:6], axis=1)/(n0*a0)
    return common, np.maximum(dr, dv)


def first_exceedance(t, delta, thresholds):
    """τ(δ): первая сохранённая метка с Δ > δ; None — не превышено на общей сетке."""
    t, delta = np.asarray(t, float), np.asarray(delta, float)
    out = {}
    for thr in thresholds:
        hit = np.nonzero(delta > thr)[0]
        out[f'{thr:g}'] = float(t[hit[0]]) if hit.size else None
    return out


def growth_fit(t, delta, lo=1e-13, hi=1e-3, min_points=5):
    """Описательно: ln Δ против t (экспонента) и против ln t (степень) на участке lo < Δ < hi до первого Δ ≥ hi."""
    t, delta = np.asarray(t, float), np.asarray(delta, float)
    stop = np.nonzero(delta >= hi)[0]
    end = stop[0] if stop.size else len(t)
    mask = np.zeros(len(t), bool)
    mask[:end] = True
    mask &= (delta > lo) & (t > 0)
    if mask.sum() < min_points:
        return dict(points=int(mask.sum()), exponential=None, power=None)
    y = np.log(delta[mask])

    def fit(x):
        slope, intercept = np.polyfit(x, y, 1)
        resid = y-(slope*x+intercept)
        total = float(np.sum((y-y.mean())**2))
        r2 = 1.0-float(np.sum(resid**2))/total if total > 0 else None
        return dict(slope=float(slope), r2=r2)
    return dict(points=int(mask.sum()), exponential=fit(t[mask]), power=fit(np.log(t[mask])),
                note='описательно; не время Ляпунова и не время потери')


def sigma_num(dense_a, dense_b, a0, n0, window_s):
    """Оценка численной погрешности: max Δ двух самых точных ступеней DOP853 при 0 < t ≤ window_s."""
    if dense_a is None or dense_b is None:
        return None
    t, d = divergence(dense_a[0], dense_a[1], dense_b[0], dense_b[1], a0, n0)
    mask = (t > 0) & (t <= window_s)
    return float(d[mask].max()) if mask.any() else None


def osculating(probe, gm):
    """a, e по относительной пробе (км, км/с); a < 0 — гипербола."""
    r = np.asarray(probe[:3], float)
    v = np.asarray(probe[3:6], float)
    rn, v2 = float(np.linalg.norm(r)), float(v @ v)
    energy = v2/2-gm/rn
    h = np.cross(r, v)
    e_vec = np.cross(v, h)/gm-r/rn
    a = -gm/(2*energy) if energy != 0 else math.inf
    return a, float(np.linalg.norm(e_vec))


def grid_diagnostics(times, probe, gm_host, hill_km, outer_factor=2.0, e_limit=0.9):
    """Диагностики по сохранённой сетке (не непрерывный максимум/минимум между метками)."""
    times = np.asarray(times, float)
    probe = np.asarray(probe, float)
    r = np.linalg.norm(probe[:, :3], axis=1)
    k = int(np.argmin(r))
    inside = r < hill_km
    inside_outer = r < outer_factor*hill_km
    elements = [osculating(p, gm_host) for p in probe]
    ecc = np.array([e for _, e in elements])
    bound = np.array([a > 0 for a, _ in elements])
    high = np.nonzero(bound & (ecc >= e_limit))[0]
    return dict(points=int(len(times)), min_distance_km=float(r[k]), min_distance_time=float(times[k]),
                rh_crossings=int(np.count_nonzero(inside[1:] != inside[:-1])),
                outer_crossings=int(np.count_nonzero(inside_outer[1:] != inside_outer[:-1])),
                max_bound_e=float(ecc[bound].max()) if bound.any() else None,
                first_bound_e_at_limit_time=float(times[high[0]]) if high.size else None,
                note='только сохранённые метки; пересечения между метками не исключены')


# ---------------------------------------------------------------- возмущения старта

def isotropic_directions(seed, count=4):
    rng = np.random.default_rng(seed)
    u = rng.standard_normal((count, 6))
    return u/np.linalg.norm(u, axis=1, keepdims=True)


def perturbation(probe, a0, n0, direction, scale, seed):
    """Смещение пробы: R/T/N — положение по радиусу/скорости/нормали на scale·a0;
    I<k> — изотропный единичный вектор в 6D с масштабами a0 и n0·a0."""
    r = np.asarray(probe[:3], float)
    v = np.asarray(probe[3:6], float)
    d = np.zeros(6)
    if direction in DIRECTIONS_NAMED:
        axes = dict(R=r/np.linalg.norm(r), T=v/np.linalg.norm(v))
        n = np.cross(r, v)
        axes['N'] = n/np.linalg.norm(n)
        sign = 1.0 if direction[0] == '+' else -1.0
        d[:3] = sign*scale*a0*axes[direction[1]]
    elif direction.startswith('I'):
        u = isotropic_directions(seed, int(direction[1:])+1)[int(direction[1:])]
        d[:3] = scale*a0*u[:3]
        d[3:] = scale*n0*a0*u[3:]
    else:
        raise ValueError('Неизвестное направление '+direction)
    return (np.concatenate([r, v])+d).tolist()


def perturbation_summary(base, rows, p0, scales):
    """По масштабам: число смен метки (направления), max |Δt|/P0 относительно невозмущённой строки."""
    out = {}
    for s in scales:
        sel = [r for r in rows if r['perturbation']['scale'] == s]
        flips = sorted(r['perturbation']['direction'] for r in sel if label(r) != label(base))
        same = [abs(outcome_time(r)-outcome_time(base))/p0 for r in sel if label(r) == label(base)]
        out[f'{s:g}'] = dict(n=len(sel), flips=len(flips), flipped_directions=flips,
                             max_dt_p0=max(same) if same else None,
                             incomplete=sum(1 for r in sel if label(r).startswith('incomplete')))
    return out


# ---------------------------------------------------------------- классы К1–К4

def classify(case, thr):
    """Класс расхождения одной единицы. case: rows (code -> row), dense (code -> (t, probe)),
    ladders, a0, n0, p0, high_e, perturb_family, perturb_rows, scales.

    Порядок К0 → К2 → К1 → К3 → К4 (W2-P015 §6, уточнения при freeze): К0 — грубая ступень
    совпадает со сошедшимся пределом (архивное расхождение здесь не воспроизвелось)."""
    rows, p0 = case['rows'], case['p0']
    dop = ladder_status(rows, case['ladders']['dop853'], p0, thr)
    ias = ladder_status(rows, case['ladders']['ias15'], p0, thr)
    finest = dop['finest'] or []
    sig = None
    if len(finest) == 2:
        dense = case.get('dense', {})
        sig = sigma_num(dense.get(finest[0]), dense.get(finest[1]), case['a0'], case['n0'],
                        thr['sigma_num_window_years']*YEAR)
    s_star = max(thr['sensitive_scale_floor'], 10*sig if sig is not None else 0.0)
    base = rows.get(case.get('perturb_family'))
    prow = case.get('perturb_rows') or []
    psum = perturbation_summary(base, prow, p0, case['scales']) if base is not None and prow else None
    small = [f'{s:g}' for s in case['scales'] if s <= s_star]
    info = dict(dop853=dop, ias15=ias, sigma_num=sig, s_star=s_star, perturbation=psum,
                families_agree=families_agree(dop, ias, p0, thr))
    # «грубая» ступень и предел: DOP853, если его самая точная ступень определена; иначе IAS15.
    use_dop = dop['status'] in ('converged', 'not_converged') and not case.get('high_e')
    coarse = case['ladders']['dop853'][0] if use_dop else case['ladders']['ias15'][0]
    limit = dop if use_dop else ias
    info['coarse'] = coarse

    # К0: грубая ступень совпадает со сошедшимся пределом — расхождение здесь не воспроизвелось.
    if (coarse in rows and limit['status'] == 'converged' and info['families_agree'] is not False
            and not differs(rows[coarse], dict(rows[limit['finest'][1]]), p0, thr['event_time_p0'])):
        return dict(info, cls='K0', confidence=None, k2=[],
                    reason='грубая ступень совпадает с пределом: расхождение не воспроизвелось на этой платформе')

    # К2: различающиеся ступени совпадают по траектории вплоть до ≤ margin·P0 до события.
    k2 = []
    lim_code = limit['finest'][1] if limit['finest'] else None
    if coarse in rows and lim_code in rows and differs(rows[coarse], rows[lim_code], p0, thr['event_time_p0']):
        events = [outcome_time(rows[c]) for c in (coarse, lim_code) if rows[c].get('event')]
        dense = case.get('dense', {})
        if events and dense.get(coarse) is not None and dense.get(lim_code) is not None:
            t, d = divergence(*dense[coarse], *dense[lim_code], case['a0'], case['n0'])
            tau = first_exceedance(t, d, [1e-6])['1e-06']
            t_ev = min(events)
            margin = thr['k2_event_margin_p0']*p0
            covered = bool(np.any((t >= t_ev-margin) & (t <= t_ev)))  # есть общая метка у события
            ok = covered and (tau is None or tau >= t_ev-margin)
            k2.append(dict(pair=[coarse, lim_code], tau_1e6=tau, event_time=t_ev, covered=covered,
                           agree_to_event=ok))
    info['k2'] = k2
    if any(x['agree_to_event'] for x in k2):
        return dict(info, cls='K2', confidence='medium',
                    reason='траектории совпадают до события; различие в классификации события')

    stable = None
    if psum is not None:
        stable = all(psum[k]['flips'] == 0 and (psum[k]['max_dt_p0'] or 0.0) <= thr['event_time_p0']
                     and psum[k]['incomplete'] == 0 for k in small)
    # К1: сходимость к пределу, грубая ступень отличается, малые возмущения устойчивы.
    agree = info['families_agree']
    converged = limit['status'] == 'converged' and agree is not False
    if use_dop and ias['status'] == 'not_converged' and ias.get('label') not in (None, limit['label']):
        converged = False
    coarse_differs = (coarse in rows and limit['status'] == 'converged'
                      and (label(rows[coarse]) != limit['label']
                           or abs(outcome_time(rows[coarse])-limit['time']) > thr['event_time_p0']*p0))
    if converged and coarse_differs and stable:
        all_scales = all(v['flips'] == 0 and (v['max_dt_p0'] or 0.0) <= thr['event_time_p0'] for v in psum.values())
        high = agree is True and all_scales and not case.get('high_e')
        return dict(info, cls='K1', confidence='high' if high else 'medium',
                    reason='семейства сходятся, грубая ступень отличается, малые возмущения устойчивы',
                    wrong=wrong_sides(rows, limit, p0, thr))

    # К3: малые возмущения меняют исход/время, лестница не сходится по метке.
    if psum is not None:
        sens = [k for k in small if psum[k]['flips'] >= thr['k3_min_directions']
                or (psum[k]['max_dt_p0'] or 0.0) > thr['k3_time_spread_p0']]
        not_conv = 'not_converged' in (dop['status'], ias['status'])
        if sens and not_conv:
            return dict(info, cls='K3', confidence='high' if f"{min(case['scales']):g}" in sens else 'medium',
                        reason='малые возмущения меняют исход или время; лестница не сходится',
                        sensitive_scales=sens)
    return dict(info, cls='K4', confidence=None, reason='ни один класс не выполнен полностью')


def wrong_sides(rows, limit, p0, thr):
    """Какие ступени отличаются от предела (метка или |Δt| > event_time_p0·P0)."""
    out = []
    for code, row in rows.items():
        if label(row) != limit['label'] or abs(outcome_time(row)-limit['time']) > thr['event_time_p0']*p0:
            out.append(code)
    return sorted(out)


# ---------------------------------------------------------------- правило режима

def decide_regime(targets, controls, b_checks):
    """W2-P015 §7. targets: [{orbit_id, high_e, cls, wrong}], controls: [{orbit_id, labels: {code: label}}],
    b_checks: [{orbit_id, labels: {B, Bs, Be}}]."""
    flags = []
    changed = [c['orbit_id'] for c in controls if len(set(c['labels'].values())) > 1]
    if changed:
        flags.append(dict(rule=4, units=changed, text='контроль меняет метку на лестнице: issue, пересмотр P2-2b'))
    b_bad = [b['orbit_id'] for b in b_checks if len(set(b['labels'].values())) > 1]
    if b_bad:
        flags.append(dict(rule=5, units=b_bad, text='B (epsilon 1e-9) не сошедшийся контроль; Фаза 2 на уточнённом B'))
    plain = [t for t in targets if not t['high_e']]
    k1 = [t for t in plain if t['cls'] == 'K1']
    t_wrong = [t['orbit_id'] for t in k1 if 'T' in (t.get('wrong') or [])]
    d_wrong = [t['orbit_id'] for t in k1 if 'D' in (t.get('wrong') or []) and 'T' not in (t.get('wrong') or [])]
    if t_wrong:
        verdict = dict(rule=2, candidate='Ts/U', units=t_wrong)
    elif d_wrong:
        verdict = dict(rule=1, candidate='T', units=d_wrong)
    elif plain and all(t['cls'] in ('K0', 'K2', 'K3') for t in plain) and any(t['cls'] != 'K0' for t in plain):
        verdict = dict(rule=3, candidate='P2-2b decides D/T', units=[t['orbit_id'] for t in plain])
    else:
        verdict = dict(rule=None, candidate='undetermined',
                       units=[t['orbit_id'] for t in plain if t['cls'] in ('K4', None)])
    verdict['not_reproduced'] = [t['orbit_id'] for t in plain if t['cls'] == 'K0']
    return dict(verdict=verdict, flags=flags, rules_version=RULES_VERSION)


# ---------------------------------------------------------------- выбор контролей (до расчётов)

def select_controls(b_rows, d_rows, t_rows, periods, excluded, cost_cap):
    """Контроли §2.4 (уточнение при freeze): по (хозяин, исход) среди согласных единиц B144.

    b_rows/d_rows/t_rows: orbit_id -> строка 1000 лет (новый B, D, T); periods: orbit_id -> P0.
    Терминальные: max-min времени трёх методов ≤ 1 P0; берётся наибольшее время события.
    survived: наименьший wall нового B. Порог стоимости — wall нового B ≤ cost_cap."""
    best, candidates = {}, {}
    for oid, b in sorted(b_rows.items()):
        if oid in excluded or oid not in d_rows or oid not in t_rows:
            continue
        rows = (b, d_rows[oid], t_rows[oid])
        lab = label(b)
        if lab.startswith(('unresolved', 'incomplete')) or any(label(r) != lab for r in rows[1:]):
            continue
        if b['wall'] > cost_cap:
            continue
        if lab == 'survived':
            key = (b['wall'], b['unit'])
        else:
            times = [float(r['event']['time']) for r in rows]
            if max(times)-min(times) > periods[oid]:
                continue
            key = (-times[0], b['unit'])
        group = (b['host'], lab)
        candidates[group] = candidates.get(group, 0)+1
        if group not in best or key < best[group][0]:
            best[group] = (key, oid)
    chosen = {f'{h}:{lab}': oid for (h, lab), (_, oid) in sorted(best.items())}
    return dict(chosen=chosen, candidates={f'{h}:{lab}': n for (h, lab), n in sorted(candidates.items())})


def perturbed_controls(chosen, b_rows):
    """По одному контролю на исход: терминальные — наибольшее время события, survived — наименьший wall B."""
    out = {}
    for outcome in ('host_contact', 'operational_escape', 'survived'):
        ids = [oid for key, oid in chosen.items() if key.split(':', 1)[1] == outcome]
        if not ids:
            continue
        if outcome == 'survived':
            out[outcome] = min(ids, key=lambda o: (b_rows[o]['wall'], o))
        else:
            out[outcome] = max(ids, key=lambda o: (float(b_rows[o]['event']['time']), o))
    return out
