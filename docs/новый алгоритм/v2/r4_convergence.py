"""R4: конвергентный тест доли выживания по ансамблю (W2, §67).

Движок S (симпалектический, фиксированный шаг) против движка A (DOP853+JIT).
Исходы и диагностики восстанавливаются ОДИНАКОВО для всех уровней шага — из
сохранённой траектории (в цикле события выключены), чтобы сравнение не зависело
от детектора событий. Это и есть корректная постановка конвергентного теста:
меняется только шаг, всё остальное фиксировано.

Разведка, не проверенный production-модуль.
"""
import os, sys, json, math, time
for _n in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ[_n] = '1'
ROOT = '/home/user/workspace/submoon-research'
sys.path.insert(0, ROOT + '/src')
sys.path.insert(0, ROOT + '/scratch')
import numpy as np
from scipy.stats import qmc
from symplectic_probe import build, CFG, YEAR, kepler_drift, pert_acceleration
from submoon_research.dynamics import fast_forces
from submoon_research.dynamics.engine_compare import integrate_engine, NumericMode
from submoon_research.dynamics.native_ias15 import MassiveCache
from submoon_research.workflows.w2_comparison import warmup

# --- параметры эксперимента ---
YEARS = 20.0
HORIZON = YEARS * YEAR
POWER = 5                 # 2^5 = 32 стартов
A_MIN, A_MAX = 0.30, 0.80  # доли радиуса Хилла
E_MAX = 0.30
LEVELS = (8, 16, 32, 64)
OUTER = 2.0                # порог ухода в единицах R_H
SEED = 20261008


def ensemble(rel, gms, hill):
    """Sobol-ансамбль по области: a, e, cos i, три фазы."""
    pts = qmc.Sobol(d=6, scramble=True, seed=SEED).random_base2(POWER)
    out = []
    for idx, p in enumerate(pts):
        a_frac = A_MIN + p[0] * (A_MAX - A_MIN)
        e = p[1] * E_MAX
        u = p[2] * 2 - 1
        M, om, node = p[3] * 2 * math.pi, p[4] * 2 * math.pi, p[5] * 2 * math.pi
        a = a_frac * hill
        if a * (1 - e) < 749.1:          # внутренняя граница (1+buffer)*(R+0.1)
            continue
        pv = np.array(elements_to_cartesian(gms[0], a, e, u, M, om, node, np.eye(3)))
        out.append(dict(index=idx, a_frac=a_frac, a_km=a, e0=e, u=u, M=M, om=om, node=node,
                        state=np.vstack([rel, pv]).ravel(),
                        period=2 * math.pi * math.sqrt(a ** 3 / gms[0])))
    return out


from submoon_research.sampling.design import elements_to_cartesian  # noqa: E402


def run_s(st, gms, figs, horizon, period, hill, cache, spo, limit=1800):
    """Симпалектический цикл без событий: сохраняет всю траекторию пробы."""
    fs = fast_forces.figure_array(figs, len(gms))
    dt = period / spo
    r = np.array(st[-6:-3], float); v = np.array(st[-3:], float)
    mu = float(gms[0]); t = 0.0
    ts, rs, vs = [], [], []
    t0 = time.perf_counter()
    while t < horizon - 1e-12:
        if time.perf_counter() - t0 > limit:
            raise TimeoutError('wall limit')
        h = min(dt, horizon - t)
        r, v = kepler_drift(r, v, h / 2, mu); t += h / 2
        massive = cache(t).reshape(-1, 6)
        acc = pert_acceleration(r, massive[:, :3], gms, fs)
        v = v + acc * h
        r, v = kepler_drift(r, v, h / 2, mu); t += h / 2
        ts.append(t); rs.append(r.copy()); vs.append(v.copy())
    return np.array(ts), np.array(rs), np.array(vs), time.perf_counter() - t0


def osculating(r, v, mu):
    rn = float(np.linalg.norm(r)); vn = float(np.linalg.norm(v))
    energy = vn ** 2 / 2 - mu / rn
    a = -mu / (2 * energy) if energy < 0 else float('inf')
    h = np.cross(r, v); hn = float(np.linalg.norm(h))
    evec = np.cross(v, h) / mu - r / rn
    e = float(np.linalg.norm(evec))
    inc = math.degrees(math.acos(max(-1.0, min(1.0, h[2] / hn)))) if hn > 0 else float('nan')
    return a, e, inc


def classify(ts, rs, radii, hill, window, contact_km):
    """Единое правило исхода для всех уровней шага."""
    dist = np.linalg.norm(rs, axis=1)
    if dist.min() <= contact_km:
        return 'host_contact', float(ts[int(np.argmin(dist))])
    outer = np.where(dist >= OUTER * hill)[0]
    if len(outer):
        first = int(outer[0])
        after = dist[first:]
        # нет возврата внутрь R_H и прошло не меньше окна
        if (after > hill).all() and (ts[-1] - ts[first]) >= window:
            return 'operational_escape', float(ts[first])
        if (ts[-1] - ts[first]) < window:
            return 'unresolved', float(ts[-1])
    if dist[-1] >= hill:
        return 'unresolved', float(ts[-1])
    return 'survived', float(ts[-1])


def run_a(st, gms, figs, radii, horizon, period, hill, window, times):
    t0 = time.perf_counter()
    res = integrate_engine('dop853_jit', st, gms, figs, radii, horizon, period=period,
                           mode=NumericMode(), hill_km=hill, escape_window=window,
                           times=times, budget=lambda: None)
    return res, time.perf_counter() - t0


def main():
    warmup()
    rel, gms, radii, figs, hill, window = build(CFG)
    contact_km = radii[0]
    cases = ensemble(rel, gms, hill)
    print(f"ensemble={len(cases)} horizon={YEARS}yr hill={hill:.0f}km window={window/86400:.1f}d "
          f"contact={contact_km:.1f}km", flush=True)
    print(f"a_frac range=[{min(c['a_frac'] for c in cases):.2f},{max(c['a_frac'] for c in cases):.2f}] "
          f"e0 max={max(c['e0'] for c in cases):.3f}", flush=True)
    # кеш массивного поля на весь горизонт
    t0 = time.perf_counter()
    cache = MassiveCache(cases[0]['state'], gms, figs, epsilon=NumericMode().cache_epsilon)
    cache.extend(HORIZON, lambda: None)
    cb = time.perf_counter() - t0
    print(f"cache build {cb:.1f}s segs={len(cache.segments)} bytes={cache.bytes/2**20:.0f}MiB", flush=True)
    times = np.linspace(0, HORIZON, 257)

    # --- движок A: эталон ---
    a_out, a_diag, a_time = [], [], []
    for c in cases:
        try:
            res, dt = run_a(c['state'], gms, figs, radii, HORIZON, c['period'], hill, window, times)
            a_out.append(res['physical_outcome']); a_time.append(dt)
            fin = np.asarray(res['final_state'][-6:], float)
            a_diag.append(osculating(fin[:3], fin[3:], gms[0])[1])
        except Exception as ex:
            a_out.append('error:' + type(ex).__name__); a_time.append(None); a_diag.append(None)
    def frac(seq, val):
        return sum(1 for x in seq if x == val) / len(seq)
    print(f"\nA (эталон): survived={frac(a_out,'survived'):.3f} escape={frac(a_out,'operational_escape'):.3f} "
          f"contact={frac(a_out,'host_contact'):.3f} unresolved={frac(a_out,'unresolved'):.3f} "
          f"total={sum(a_time)/60:.1f}min", flush=True)

    # --- движок S: сходимость по шагу ---
    table = []
    for spo in LEVELS:
        outs, diag_e, diag_inc, tsum, fails = [], [], [], 0.0, 0
        for c in cases:
            try:
                ts, rs, vs, dt = run_s(c['state'], gms, figs, HORIZON, c['period'], hill, cache, spo)
                tsum += dt
                oc, _ = classify(ts, rs, radii, hill, window, contact_km)
                outs.append(oc)
                a_f, e_f, i_f = osculating(rs[-1], vs[-1], gms[0])
                diag_e.append(e_f); diag_inc.append(i_f)
            except Exception as ex:
                fails += 1; outs.append('error:' + type(ex).__name__)
        n = len(outs)
        row = dict(steps_per_orbit=spo, dt_days=(c['period'] / spo) / 86400,
                   survived=frac(outs, 'survived'), escape=frac(outs, 'operational_escape'),
                   contact=frac(outs, 'host_contact'), unresolved=frac(outs, 'unresolved'),
                   errors=fails, seconds=tsum,
                   e_final_median=float(np.median(diag_e)) if diag_e else None,
                   inc_final_median=float(np.median(diag_inc)) if diag_inc else None)
        # биномиальная ошибка доли выживания
        p = row['survived']; row['survived_stderr'] = math.sqrt(p * (1 - p) / n)
        table.append(row)
        print(f"S {spo:3d}/orbit dt={row['dt_days']:7.4f}d | survived={row['survived']:.3f}"
              f"±{row['survived_stderr']:.3f} escape={row['escape']:.3f} contact={row['contact']:.3f}"
              f" unres={row['unresolved']:.3f} err={fails} | e_med={row['e_final_median']} "
              f"i_med={row['inc_final_median']} | {tsum:.1f}s", flush=True)

    out = dict(meta=dict(years=YEARS, ensemble=len(cases), hill_km=hill, window_days=window / 86400,
                         contact_km=contact_km, seed=SEED, a_min=A_MIN, a_max=A_MAX, e_max=E_MAX,
                         outer_factor=OUTER, cache_bytes=cache.bytes, cache_segments=len(cache.segments)),
               engine_A=dict(survived=frac(a_out, 'survived'), escape=frac(a_out, 'operational_escape'),
                             contact=frac(a_out, 'host_contact'), unresolved=frac(a_out, 'unresolved'),
                             e_final_median=float(np.median([x for x in a_diag if x is not None])),
                             outcomes=a_out, seconds=a_time),
               engine_S=table,
               per_case=[dict(index=c['index'], a_frac=c['a_frac'], e0=c['e0'],
                              period_days=c['period'] / 86400) for c in cases])
    with open(ROOT + '/scratch/r4_convergence.json', 'w') as f:
        json.dump(out, f, indent=1)
    print("\nDONE", flush=True)


if __name__ == '__main__':
    main()
