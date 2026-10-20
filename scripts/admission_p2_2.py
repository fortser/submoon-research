"""W2-P006, ступень P2-2a: эквивалентность долей исходов D и T на вероятностной выборке W1.

Критерий зафиксирован до расчёта: docs/tasks/W2_P2-2_plan_v1.md §2–4, §6–7.
Единица — независимая рандомизация из одной точки: generate(power=0, seed=205610051+9j+c,
randomization=j) генератора W1; 3 хозяина × 3 меры = 9 ячеек по --per-cell единиц.
Задания: D (NumericMode) и T (NumericMode.tight) на каждом горизонте --years отдельно (расчёт
не зависит от горизонта, W2-C035); B (IAS15/REBOUNDx) — на единицах j < unit_start + --b-per-cell
на горизонтах --b-years. Прототип v5 (правило высоких e W2-D022), предел шага B (W2-I019).

Расхождение единицы — разные метки исхода: survived, host_contact, other_contact,
operational_escape, unresolved с причиной (канал потерь). Для каждой страты из S (сводно S=1,
хозяева S=3, ячейки S=9): «эквивалентно» — точная односторонняя (Клоппер–Пирсон) верхняя
граница доли расхождений D–T при α=0.05/S не больше 0.005; «не эквивалентно» — двусторонний
интервал Ньюкомба для парных долей уровня 1−0.05/(5S) для какой-либо из 5 категорий целиком вне
[−0.005, 0.005]; иначе «не определено». Решение P2-2a — только сводно на финальном горизонте
полного объёма, когда все задания D и T там есть, без остановок и отказов (§4, §7; W2-D026:
остановленные сроком или недостающие задания B решение не блокируют и показываются отдельно);
остальное — описательно. B — согласие независимым методом (точные интервалы), не допуск.

Каждое задание сразу дописывается в jobs.jsonl; продолжение — по отпечатку задания (единица,
физика, режим, горизонт, вариант, сетка, SHA реализации). --deadline-utc и --stop-file
останавливают задания (partial: ступень не завершена, при продолжении они пересчитываются).
--evaluate-only — разбор готового jobs.jsonl. Порядок заданий — самые долгие первыми (по модели
стоимости плана). --workers 0 — в текущем процессе (тесты).

    ~/venvs/submoon-w2/bin/python scripts/admission_p2_2.py --workers 16
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import multiprocessing as mp
import os
import sys
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402
import yaml  # noqa: E402
from scipy.stats import beta, norm  # noqa: E402

from admission_p2_horizons import (  # noqa: E402
    STOPPED, Budget, _init, b_fingerprint, parse_deadline, read_rows, unresolved_reason)
from bench_engines_ac import cpu_info  # noqa: E402
from plan_p2_2 import HOSTS, SEED_BASE, STEPS_PER_ORBIT, US_PER_STEP  # noqa: E402
from submoon_research.catalog.nominal_model import validate_model  # noqa: E402
from submoon_research.contracts.nominal_states import validate_nominal_states  # noqa: E402
from submoon_research.dynamics.compiled_dop853 import (  # noqa: E402
    PROTOTYPE_VERSION, implementation_fingerprint, integrate_compiled)
from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine  # noqa: E402
from submoon_research.dynamics.initialization import PARENTS, physical_inputs  # noqa: E402
from submoon_research.sampling.design import MEASURES, DomainSpec, generate  # noqa: E402
from submoon_research.workflows.smoke import sha256 as file_sha256  # noqa: E402
from submoon_research.exact_linalg import NUMERICS_VERSION  # noqa: E402
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, contract_digest, digest, exclusive_run, overlaps, preflight, read_json, source_manifest,
    units_digest, validate_units_document,
)
from submoon_research.contracts.frozen_p2 import (  # noqa: E402
    copied_provenance, load_frozen_inputs, manifest_for,
)

PLAN, STAGE, STAND_VERSION = 'W2-P006', 'P2-2a', 'P2-2-v1'
# Версия правила разбора (не входит в отпечаток заданий: счёт не меняется). a2 — W2-D026.
ANALYSIS_VERSION = 'P2-2-a2'
YEAR = 365.25*86400.0
CELLS = tuple((host, measure) for host in HOSTS for measure in MEASURES)
FULL = dict(per_cell=128, b_per_cell=16, unit_start=0, years=[1.0, 10.0, 100.0, 1000.0],
            b_years=[100.0, 1000.0])
MARGIN, ALPHA = 0.005, 0.05
CATEGORIES = ('survived', 'host_contact', 'other_contact', 'operational_escape', 'unresolved')
LEVELS = (('pooled', 1), ('hosts', 3), ('cells', 9))
DECISION_LEVEL = 'pooled'
PAIRS = (('D', 'T'), ('D', 'B'), ('T', 'B'))
DIVERGENCE = 1e-6  # порог τ (|Δr|/a или |Δv|/(na)), как в P2-1 — только описательно
GRID_ID = '0+geomspace31(1e-5H..H)'
VERDICT_RU = dict(equivalent='ЭКВИВАЛЕНТНО', not_equivalent='НЕ ЭКВИВАЛЕНТНО',
                  inconclusive='НЕ ОПРЕДЕЛЕНО', not_decided='РЕШЕНИЯ НЕТ')


# ---------------------------------------------------------------- выборка и задания

def setups(root=ROOT):
    """Хозяева W1: те же входы и области, что load_cases (e <= 0.3, буфер 0.02)."""
    cfg = yaml.safe_load((root/'configs/experiments/W2_three_engines_v1.yaml').read_text(encoding='utf-8'))
    for path in (cfg['model_path'], cfg['states_path']):
        if file_sha256(root/path) != cfg['inputs_sha256'][path]:
            raise ValueError('Изменился закреплённый вход '+path)
    model = validate_model(json.loads((root/cfg['model_path']).read_text(encoding='utf-8')))
    states = validate_nominal_states(json.loads((root/cfg['states_path']).read_text(encoding='utf-8')))
    out = {}
    for host in HOSTS:
        _, massive, gms, figures, radii, a_host, basis = physical_inputs(host, model, states)
        gm_parent = model['bodies'][PARENTS[host]]['gm']['value']
        domain = DomainSpec(domain_id=host+'-nominal-v1', gm_host=gms[0], gm_parent=gm_parent,
                            a_host_km=a_host, reference_radius_km=radii[0]-model['submoon_radius_km'],
                            submoon_radius_km=model['submoon_radius_km'], eccentricity_max=0.3,
                            contact_buffer=0.02)
        out[host] = dict(domain=domain, basis=basis, massive=np.asarray(massive), gms=gms.tolist(),
                         figures=figures, radii=[float(r) for r in radii], hill_km=domain.hill_km,
                         escape_window=2*math.pi*math.sqrt(a_host**3/(gm_parent+gms[0])))
    return out


def units(per_cell, unit_start=0, prepared=None):
    """Единицы j = unit_start … unit_start+per_cell−1 по 9 ячейкам; seed = SEED_BASE + 9j + c."""
    prepared = prepared or setups()
    out = []
    for j in range(unit_start, unit_start+per_cell):
        for c, (host, measure) in enumerate(CELLS):
            setup = prepared[host]
            seed = SEED_BASE + len(CELLS)*j + c
            record = generate(setup['domain'], measure, power=0, seed=seed, randomization=j,
                              basis=setup['basis'])['records'][0]
            el = record['elements']
            probe = list(record['position'])+list(record['velocity'])
            out.append(dict(orbit_id=f'{host}:{measure}:{j:04d}', host=host, measure=measure, cell=c,
                            unit=j, seed=seed, a_km=float(el['a_km']), eccentricity=float(el['e']),
                            cos_i=float(el['cos_i']), a_over_rh=float(el['a_km'])/setup['hill_km'],
                            state=np.vstack([setup['massive'], probe]).ravel().tolist(),
                            gms=setup['gms'], figures=setup['figures'], radii=setup['radii'],
                            hill_km=setup['hill_km'], escape_window=setup['escape_window'],
                            period_seconds=2*math.pi*math.sqrt(el['a_km']**3/setup['domain'].gm_host)))
    return out


def normalize_params(per_cell, b_per_cell, unit_start, years, b_years):
    params = dict(per_cell=int(per_cell), b_per_cell=int(b_per_cell), unit_start=int(unit_start),
                  years=sorted({float(y) for y in years}), b_years=sorted({float(y) for y in b_years}))
    if params['per_cell'] < 1 or not 0 <= params['b_per_cell'] <= params['per_cell'] or params['unit_start'] < 0:
        raise ValueError('Нужно per_cell >= 1, 0 <= b_per_cell <= per_cell, unit_start >= 0')
    if (not params['years'] or any(not math.isfinite(y) or y <= 0 for y in params['years'])
            or not set(params['b_years']) <= set(params['years'])):
        raise ValueError('Горизонты должны быть > 0, горизонты B — подмножество --years')
    return params


def is_full_scope(params):
    return all(params[k] == FULL[k] for k in FULL)


def plan_keys(unit_list, params):
    """Ожидаемый состав по горизонтам: D и T на всех единицах, B — на первых b_per_cell."""
    b_limit = params['unit_start'] + params['b_per_cell']
    planned = {}
    for years in params['years']:
        b_here = years in params['b_years']
        planned[years] = [(u['orbit_id'], years, v) for u in unit_list
                          for v in (('D', 'T', 'B') if b_here and u['unit'] < b_limit else ('D', 'T'))]
    return planned


def job_fingerprint(unit, years, variant, implementations):
    mode = NumericMode.tight() if variant == 'T' else NumericMode()
    payload = dict(stand=STAND_VERSION, orbit_id=unit['orbit_id'], seed=unit['seed'], state=unit['state'],
                   gms=unit['gms'], figures=unit['figures'], radii=unit['radii'], hill_km=unit['hill_km'],
                   escape_window=unit['escape_window'], period=unit['period_seconds'], years=float(years),
                   variant=variant, mode=mode.__dict__, grid=GRID_ID,
                   implementation=implementations['B' if variant == 'B' else 'compiled'])
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=float).encode()).hexdigest()


def shown(path):
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def grid(horizon):
    return np.unique(np.concatenate([[0.0], np.geomspace(1e-5*horizon, horizon, 31)]))


def model_seconds(unit, model_years, variant):
    """Модель стоимости плана (§5): шаг упирается в долю периода."""
    return (model_years*YEAR/unit['period_seconds']*STEPS_PER_ORBIT[variant]
            * US_PER_STEP[variant][unit['host']]*1e-6)


def _job(job):
    unit, years, variant = job['unit'], job['years'], job['variant']
    horizon = years*YEAR
    mode = NumericMode.tight() if variant == 'T' else NumericMode()
    kwargs = dict(period=unit['period_seconds'], mode=mode, hill_km=unit['hill_km'],
                  escape_window=unit['escape_window'], times=grid(horizon),
                  budget=Budget(job.get('deadline'), job.get('stop_file')))
    base = dict(orbit_id=unit['orbit_id'], host=unit['host'], measure=unit['measure'], unit=unit['unit'],
                seed=unit['seed'], years=years, variant=variant, stand_version=STAND_VERSION,
                prototype_version=PROTOTYPE_VERSION, job_fingerprint=job['fingerprint'])
    started, cpu = time.perf_counter(), time.process_time()
    try:
        if variant == 'B':
            out = integrate_engine('ias15_reboundx', unit['state'], unit['gms'], unit['figures'],
                                   unit['radii'], horizon, **kwargs)
        else:
            out = integrate_compiled(unit['state'], unit['gms'], unit['figures'], unit['radii'],
                                     horizon, **kwargs)
    except Exception as exc:  # noqa: BLE001 — сбой одного задания записывается, стенд продолжает
        probe = unit['state'][-6:]
        return dict(base, wall=time.perf_counter()-started, cpu=time.process_time()-cpu,
                    run_status='failed', error=f'{type(exc).__name__}: {exc}',
                    traceback=traceback.format_exc(), outcome='unresolved',
                    unresolved_reason='computational', last_valid_time=0.0, event=None, steps=None,
                    nfev=None, eccentricity=None, numerical_domain=None, endpoint_residual=None,
                    stopped=False, crashed=True, final_probe=probe, samples=[(0.0, probe)])
    event = out['event']
    return dict(base, wall=time.perf_counter()-started, cpu=time.process_time()-cpu,
                run_status=out['run_status'], error=out['error'], outcome=out['physical_outcome'],
                unresolved_reason=unresolved_reason(out), last_valid_time=out['last_valid_time'],
                event=None if event is None else dict(event=event['event'], time=event['time'],
                                                      body=event.get('body_index')),
                steps=out['steps'], nfev=out['nfev'], eccentricity=out.get('eccentricity_monitor'),
                numerical_domain=out.get('numerical_domain'), endpoint_residual=out.get('endpoint_residual'),
                stopped=out['run_status'] == 'partial' and STOPPED in str(out['error']),
                final_probe=out['final_state'][-6:],
                samples=[(s['time'], s['state'][-6:]) for s in out['samples']])


def run_jobs(jobs, workers, jobs_file, done, run_dir, planned, started):
    """Пул spawn (workers=0 — в текущем процессе); результат сразу дописывается в jobs.jsonl,
    progress.json обновляется после каждого задания."""
    progress = run_dir/'progress.json'

    def write_progress(last=None):
        by_level = {f'{y:g}': dict(done=sum(1 for k in keys if k in done), planned=len(keys),
                                   stopped=sum(1 for k in keys if k in done and done[k].get('stopped')))
                    for y, keys in planned.items()}
        tmp = progress.with_suffix('.tmp')
        tmp.write_text(json.dumps(dict(updated_utc=datetime.now(timezone.utc).isoformat(),
            elapsed_seconds=time.perf_counter()-started, done=len(done),
            planned=sum(len(k) for k in planned.values()), by_level=by_level, last=last),
            ensure_ascii=False, indent=1), encoding='utf-8')
        tmp.replace(progress)

    write_progress()
    if not jobs:
        return
    printed = time.perf_counter()
    with contextlib.ExitStack() as stack:
        stream = stack.enter_context(jobs_file.open('a', encoding='utf-8'))
        if workers > 0:
            pool = stack.enter_context(mp.get_context('spawn').Pool(workers, initializer=_init))
            results = pool.imap_unordered(_job, jobs, chunksize=1)
        else:
            _init()
            results = map(_job, jobs)
        for k, res in enumerate(results, 1):
            stream.write(json.dumps(res, ensure_ascii=False, default=float)+'\n')
            stream.flush()
            done[(res['orbit_id'], res['years'], res['variant'])] = res
            write_progress(dict(orbit_id=res['orbit_id'], years=res['years'], variant=res['variant'],
                                status=res['run_status'], wall=res['wall']))
            now = time.perf_counter()
            if k == len(jobs) or k % 200 == 0 or now-printed > 600:
                printed = now
                print(f'  {k}/{len(jobs)} заданий, {now-started:.0f} с с начала', flush=True)


# ---------------------------------------------------------------- статистика

def label(row):
    """Метка исхода для расхождения: категория, у unresolved — с причиной (канал потерь)."""
    if row['outcome'] != 'unresolved':
        return row['outcome']
    return 'unresolved:'+(row.get('unresolved_reason') or 'unspecified')


def cp_upper(x, n, alpha):
    """Точная односторонняя (Клоппер–Пирсон) верхняя граница биномиальной доли уровня 1−α."""
    return 1.0 if x >= n else float(beta.ppf(1-alpha, x+1, n-x))


def cp_interval(x, n, alpha):
    """Точный двусторонний интервал Клоппера–Пирсона уровня 1−α."""
    lower = 0.0 if x == 0 else float(beta.ppf(alpha/2, x, n-x+1))
    upper = 1.0 if x >= n else float(beta.ppf(1-alpha/2, x+1, n-x))
    return [lower, upper]


def wilson(x, n, z):
    centre = (x + z*z/2)/(n + z*z)
    half = z/(n + z*z)*math.sqrt(x*(n-x)/n + z*z/4)
    return max(0.0, centre-half), min(1.0, centre+half)


def newcombe_paired(n11, n10, n01, n00, alpha):
    """Интервал Ньюкомба (1998, метод 10: MOVER-Wilson; поправка φ — Fagerland et al. 2014)
    уровня 1−α для разности парных долей p_a − p_b, p_a = (n11+n10)/n, p_b = (n11+n01)/n."""
    n = n11+n10+n01+n00
    z = float(norm.ppf(1-alpha/2))
    pa, pb = (n11+n10)/n, (n11+n01)/n
    la, ua = wilson(n11+n10, n, z)
    lb, ub = wilson(n11+n01, n, z)
    e, f, g, h = n11+n10, n01+n00, n11+n01, n10+n00
    cross = n11*n00 - n10*n01
    if e*f*g*h == 0:
        phi = 0.0
    else:
        phi = (max(cross - n/2, 0.0) if cross > 0 else cross)/math.sqrt(float(e)*f*g*h)
    delta = pa-pb
    lower = delta - math.sqrt(max(0.0, (pa-la)**2 - 2*phi*(pa-la)*(ub-pb) + (ub-pb)**2))
    upper = delta + math.sqrt(max(0.0, (pb-lb)**2 - 2*phi*(pb-lb)*(ua-pa) + (ua-pa)**2))
    return delta, lower, upper


def stratum_of(unit, level):
    return 'all' if level == 'pooled' else unit['host'] if level == 'hosts' else f"{unit['host']}:{unit['measure']}"


def stratum_stats(name, items, strata, decision_pair):
    n = len(items)
    discordant = sum(1 for _, ra, rb in items if label(ra) != label(rb))
    categories = {}
    for k in CATEGORIES:
        a = [ra['outcome'] == k for _, ra, _ in items]
        b = [rb['outcome'] == k for _, _, rb in items]
        n11 = sum(x and y for x, y in zip(a, b))
        n10 = sum(x and not y for x, y in zip(a, b))
        n01 = sum(y and not x for x, y in zip(a, b))
        delta, lower, upper = newcombe_paired(n11, n10, n01, n-n11-n10-n01, ALPHA/(len(CATEGORIES)*strata))
        categories[k] = dict(count_a=n11+n10, count_b=n11+n01, fraction_a=(n11+n10)/n,
                             fraction_b=(n11+n01)/n, delta=delta, newcombe=[lower, upper])
    upper = cp_upper(discordant, n, ALPHA/strata)
    row = dict(stratum=name, n=n, discordant=discordant, discordance=discordant/n,
               alpha_cp=ALPHA/strata, cp_upper=upper, cp_two_sided=cp_interval(discordant, n, ALPHA/strata),
               alpha_newcombe=ALPHA/(len(CATEGORIES)*strata), categories=categories)
    if decision_pair:
        row['verdict'] = ('equivalent' if upper <= MARGIN else 'not_equivalent'
                          if any(c['newcombe'][0] > MARGIN or c['newcombe'][1] < -MARGIN
                                 for c in categories.values()) else 'inconclusive')
    return row


def quantiles(values, qs=(0.0, 0.1, 0.5, 0.9, 1.0)):
    return None if not values else [float(v) for v in np.quantile(values, qs)]


def divergence_time(unit, ra, rb):
    """Первое общее время сетки, где |Δr|/a или |Δv|/(na) > 1e-6 (сетка грубая — оценка сверху)."""
    na = 2*math.pi*unit['a_km']/unit['period_seconds']
    other = {t: s for t, s in rb['samples']}
    for t, s in ra['samples']:
        if t in other:
            dr = float(np.linalg.norm(np.subtract(s[:3], other[t][:3])))/unit['a_km']
            dv = float(np.linalg.norm(np.subtract(s[3:], other[t][3:])))/na
            if max(dr, dv) > DIVERGENCE:
                return t
    return None


def high_e_related(row):
    return str(row.get('unresolved_reason') or '').startswith('high_e')


def pair_summary(items, decision_pair):
    levels = {}
    for level, strata in LEVELS:
        groups = {}
        for item in items:
            groups.setdefault(stratum_of(item[0], level), []).append(item)
        levels[level] = [stratum_stats(name, groups[name], strata, decision_pair) for name in sorted(groups)]
    discordant = [(u, ra, rb) for u, ra, rb in items if label(ra) != label(rb)]
    event_dt = [abs(ra['event']['time']-rb['event']['time'])/u['period_seconds'] for u, ra, rb in items
                if ra['event'] and rb['event'] and ra['event']['event'] == rb['event']['event']
                and ra['event']['body'] == rb['event']['body']]
    taus = [divergence_time(u, ra, rb) for u, ra, rb in items]
    out = dict(units=len(items), levels=levels,
               transitions=dict(sorted(Counter(f'{label(ra)} -> {label(rb)}' for _, ra, rb in discordant).items())),
               discordant_units=[u['orbit_id'] for u, _, _ in discordant][:200],
               event_time=dict(pairs=len(event_dt), dt_periods_quantiles=quantiles(event_dt),
                               over_1e_6=sum(1 for x in event_dt if x > 1e-6)),
               divergence=dict(threshold=DIVERGENCE, grid=GRID_ID, none=sum(1 for t in taus if t is None),
                               tau_years_quantiles=quantiles([t/YEAR for t in taus if t is not None])))
    if not decision_pair:
        # У B нет правила высоких e (W2-D022): отдельно — без единиц с unresolved по высоким e.
        kept = [(u, ra, rb) for u, ra, rb in items if not high_e_related(ra) and not high_e_related(rb)]
        x = sum(1 for _, ra, rb in kept if label(ra) != label(rb))
        out['excluding_high_e'] = dict(n=len(kept), discordant=x,
                                       cp_two_sided=cp_interval(x, len(kept), ALPHA) if kept else None)
    return out


def outcome_table(rows, by_id):
    table = {}
    for row in rows:
        unit = by_id[row['orbit_id']]
        variant = table.setdefault(row['variant'], {})
        for level, _ in LEVELS:
            variant.setdefault(stratum_of(unit, level), Counter())[label(row)] += 1
    return {v: {s: dict(sorted(c.items())) for s, c in sorted(strata.items())} for v, strata in sorted(table.items())}


def cost_summary(rows, by_id):
    """Измеренная стоимость против модели плана (§5) при фактическом времени счёта."""
    out = {}
    for variant in sorted({r['variant'] for r in rows}):
        sub = [r for r in rows if r['variant'] == variant and r.get('steps')]
        if not sub:
            continue
        wall, steps = sum(r['wall'] for r in sub), sum(r['steps'] for r in sub)
        model = sum(model_seconds(by_id[r['orbit_id']], r['last_valid_time']/YEAR, variant) for r in sub)
        per_orbit = [r['steps']/(r['last_valid_time']/by_id[r['orbit_id']]['period_seconds']) for r in sub
                     if r['last_valid_time'] >= 10*by_id[r['orbit_id']]['period_seconds']]
        hosts = {}
        for host in HOSTS:
            h = [r for r in sub if r['host'] == host]
            if h:
                hosts[host] = 1e6*sum(r['wall'] for r in h)/max(1, sum(r['steps'] for r in h))
        longest = max(sub, key=lambda r: r['wall'])
        out[variant] = dict(jobs=len(sub), core_hours=wall/3600, model_core_hours=model/3600,
                            measured_over_model=wall/model if model > 0 else None, steps=steps,
                            us_per_step=1e6*wall/steps, us_per_step_by_host=hosts,
                            steps_per_orbit_quantiles=quantiles(per_orbit),
                            max_wall_hours=longest['wall']/3600,
                            max_wall_job=[longest['orbit_id'], longest['years'], variant])
    return out


def analyze(rows, unit_list, params, full_scope):
    """Разбор по §3: расхождения D–T по стратам и горизонтам, согласие с B, доли, стоимость."""
    by_id = {u['orbit_id']: u for u in unit_list}
    by_key = {(r['orbit_id'], float(r['years']), r['variant']): r for r in rows}
    planned = plan_keys(unit_list, params)
    horizons, complete_all = {}, True
    for years in params['years']:
        keys = planned[years]
        present = [by_key[k] for k in keys if k in by_key]
        stopped = [r for r in present if r['run_status'] == 'partial']
        failed = [r for r in present if r['run_status'] == 'failed']
        complete = len(present) == len(keys) and not stopped
        complete_all = complete_all and complete
        # W2-D026: завершённость D/T отдельно от B (B не входит в критерий решения).
        dt_keys = [k for k in keys if k[2] in ('D', 'T')]
        dt_complete = all(k in by_key and by_key[k]['run_status'] != 'partial' for k in dt_keys)
        b_keys = [k for k in keys if k[2] == 'B']
        b_unfinished = sum(1 for k in b_keys if k not in by_key or by_key[k]['run_status'] == 'partial')
        pairs = {}
        for a, b in PAIRS:
            items = []
            for unit in unit_list:
                ra, rb = by_key.get((unit['orbit_id'], years, a)), by_key.get((unit['orbit_id'], years, b))
                if ra is not None and rb is not None and 'partial' not in (ra['run_status'], rb['run_status']):
                    items.append((unit, ra, rb))
            if items:
                pairs[f'{a}-{b}'] = pair_summary(items, (a, b) == ('D', 'T'))
        horizons[f'{years:g}'] = dict(years=years, planned=len(keys), present=len(present),
            stopped=len(stopped), complete=complete, dt_complete=dt_complete,
            b_planned=len(b_keys), b_unfinished=b_unfinished,
            failed=[dict(orbit_id=r['orbit_id'], variant=r['variant'], error=str(r['error'])[:300]) for r in failed],
            outcomes=outcome_table(present, by_id), pairs=pairs)
    final = max(params['years'])
    level = horizons[f'{final:g}']
    pooled = (level['pairs'].get('D-T') or {}).get('levels', {}).get(DECISION_LEVEL, [None])[0]
    reasons = []
    if not full_scope:
        reasons.append('сокращённый объём — не решение P2-2a')
    if not level['dt_complete']:
        reasons.append('финальный горизонт D/T не завершён (недостающие или остановленные задания D/T)')
    if any(f['variant'] in ('D', 'T') for f in level['failed']):
        reasons.append('отказы заданий D/T на финальном горизонте')
    if pooled is None:
        reasons.append('нет пар D–T')
    notes = []
    if level['b_unfinished']:
        notes.append(f"B не завершено на финальном горизонте: {level['b_unfinished']}/{level['b_planned']} "
                     '(остановлено сроком или нет строки); решение не блокирует (W2-D026), '
                     'пары D–B/T–B — по завершённым')
    decision = dict(stage=STAGE, horizon_years=final, level=DECISION_LEVEL, margin=MARGIN,
                    rule='W2-D026', verdict='not_decided' if reasons else pooled['verdict'],
                    stratum_verdict=None if pooled is None else pooled['verdict'], reasons=reasons,
                    notes=notes, b_unfinished=level['b_unfinished'],
                    n=None if pooled is None else pooled['n'],
                    discordant=None if pooled is None else pooled['discordant'],
                    cp_upper=None if pooled is None else pooled['cp_upper'])
    return dict(plan=PLAN, stage=STAGE, stand_version=STAND_VERSION, analysis_version=ANALYSIS_VERSION,
                margin=MARGIN, alpha=ALPHA,
                categories=list(CATEGORIES), levels=[dict(level=k, strata=s) for k, s in LEVELS],
                params=params, full_scope=full_scope, complete=complete_all, horizons=horizons,
                cost=cost_summary(list(by_key.values()), by_id), decision=decision)


def report(analysis):
    for name, level in analysis['horizons'].items():
        print(f"\n=== {name} лет: заданий {level['present']}/{level['planned']}; остановлено {level['stopped']}; "
              f"отказов {len(level['failed'])}; {'завершено' if level['complete'] else 'НЕ ЗАВЕРШЕНО'}")
        for variant, table in level['outcomes'].items():
            total = table.get('all', {})
            n = sum(total.values())
            print(f"  {variant}: " + ', '.join(f'{k} {v/n:.3f}' for k, v in total.items()) + f' (n={n})')
        for pair, data in level['pairs'].items():
            pooled = data['levels']['pooled'][0]
            line = (f"  {pair}: n={pooled['n']}, расхождений {pooled['discordant']} "
                    f"(граница {pooled['cp_upper']:.4f} при α={pooled['alpha_cp']:.3g})")
            if 'verdict' in pooled:
                line += f" -> {VERDICT_RU[pooled['verdict']]}"
                worst = {k: max(s['cp_upper'] for s in data['levels'][k]) for k in ('hosts', 'cells')}
                line += f"; макс. граница: хозяева {worst['hosts']:.4f}, ячейки {worst['cells']:.4f}"
            if 'excluding_high_e' in data:
                ex = data['excluding_high_e']
                line += f"; без высоких e {ex['discordant']}/{ex['n']}"
            print(line)
            if data['transitions']:
                print('     переходы: ' + '; '.join(f'{k}: {v}' for k, v in data['transitions'].items()))
            ev, dv = data['event_time'], data['divergence']
            if ev['pairs']:
                q = ev['dt_periods_quantiles']
                print(f"     события одного типа: {ev['pairs']}, |Δt|/P0 медиана {q[2]:.2e}, макс {q[4]:.2e}, "
                      f"> 1e-6: {ev['over_1e_6']}")
            if dv['tau_years_quantiles']:
                q = dv['tau_years_quantiles']
                print(f"     τ (>1e-6, грубая сетка): мин {q[0]:.3g}, медиана {q[2]:.3g}, макс {q[4]:.3g} лет; "
                      f"без расхождения {dv['none']}")
        for failure in level['failed'][:10]:
            print(f"  ОТКАЗ: {failure['orbit_id']} {failure['variant']}: {failure['error'][:160]}")
    for variant, cost in analysis['cost'].items():
        ratio = cost['measured_over_model']
        print(f"\nстоимость {variant}: заданий {cost['jobs']}, {cost['core_hours']:.3f} ядро-ч "
              f"(модель плана {cost['model_core_hours']:.3f}, факт/модель {'—' if ratio is None else f'{ratio:.2f}'}); "
              f"{cost['us_per_step']:.1f} мкс/шаг; макс. задание {cost['max_wall_hours']:.3f} ч {cost['max_wall_job']}")
    d = analysis['decision']
    print(f"\nРЕШЕНИЕ {d['stage']} ({d['level']}, {d['horizon_years']:g} лет, граница {d['margin']}): "
          f"{VERDICT_RU[d['verdict']]}" + (f" — {'; '.join(d['reasons'])}" if d['reasons'] else '')
          + (f"; по данным страты: {VERDICT_RU[d['stratum_verdict']]}" if d['reasons'] and d['stratum_verdict'] else ''))
    for note in d.get('notes') or []:
        print('  примечание: '+note)


# ---------------------------------------------------------------- запуск

def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--per-cell', type=int, default=FULL['per_cell'])
    parser.add_argument('--b-per-cell', type=int, default=FULL['b_per_cell'])
    parser.add_argument('--unit-start', type=int, default=FULL['unit_start'])
    parser.add_argument('--years', type=float, nargs='+', default=FULL['years'])
    parser.add_argument('--b-years', type=float, nargs='*', default=FULL['b_years'])
    parser.add_argument('--workers', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--run-dir', default='scratch/claude/W2-P006_P2-2a')
    parser.add_argument('--deadline-utc', default=None,
                        help='ISO 8601 UTC: к этому сроку незавершённые задания останавливаются (partial)')
    parser.add_argument('--stop-file', default=None,
                        help='если файл появится, задания останавливаются (partial) и стенд подводит итог')
    parser.add_argument('--inputs-manifest', help='закреплённые входы вместо вызова генератора')
    parser.add_argument('--inputs-manifest-sha256', help='внешний SHA-256 манифеста входов')
    parser.add_argument('--evaluate-only', action='store_true')
    parser.add_argument('--resume', action='store_true', help='продолжить незавершённый совместимый run')
    parser.add_argument('--analysis-dir', default=None,
                        help='новый отдельный каталог; обязателен и разрешён только с --evaluate-only')
    return parser.parse_args(argv)


def units_document(unit_list, params, implementations=None):
    """Полный контракт новых run; legacy-хеш стартов сохраняет прежний смысл."""
    doc = dict(plan=PLAN, stage=STAGE, generator_numerics=NUMERICS_VERSION, params=params,
               units_sha256=units_digest(unit_list), units=unit_list,
               contract=dict(version='p2-input-v1', stand=STAND_VERSION, grid=GRID_ID,
                             modes=dict(D=NumericMode().__dict__, T=NumericMode.tight().__dict__),
                             implementations=implementations,
                             position_unit='km', time_unit='s', gm_unit='km^3/s^2',
                             frame='host-relative, nonrotating ICRF axes',
                             input_config_sha256=file_sha256(ROOT/'configs/experiments/W2_three_engines_v1.yaml')))
    doc['contract_sha256'] = contract_digest(doc)
    return doc


def main(argv=None):
    args = parse(argv)
    try:
        with exclusive_run(ROOT/args.run_dir, ROOT/'scratch/p2_locks'):
            return _main(args)
    except (OSError, ValueError) as exc:
        traceback.print_exc()
        print(f'ОТКАЗ/ОШИБКА СТЕНДА: {exc}', flush=True)
        return 2


def _main(args):
    params = normalize_params(args.per_cell, args.b_per_cell, args.unit_start, args.years, args.b_years)
    full_scope = is_full_scope(params)
    run_dir = (ROOT/args.run_dir).resolve()
    jobs_file = run_dir/'jobs.jsonl'
    deadline = parse_deadline(args.deadline_utc)
    implementations = dict(compiled=implementation_fingerprint(), B=b_fingerprint())
    if bool(args.inputs_manifest) != bool(args.inputs_manifest_sha256):
        raise ValueError('Нужны вместе --inputs-manifest и --inputs-manifest-sha256')
    if args.inputs_manifest:
        input_dir = (ROOT/args.inputs_manifest).resolve().parent
        if overlaps(run_dir, input_dir) and not (run_dir == input_dir and (args.resume or args.evaluate_only)):
            raise ValueError('Run не должен изменять отдельный артефакт frozen inputs')
        if args.analysis_dir and overlaps(ROOT/args.analysis_dir, input_dir):
            raise ValueError('Анализ не должен изменять frozen inputs')
        document, _ = load_frozen_inputs(ROOT/args.inputs_manifest, args.inputs_manifest_sha256,
            params=params, numerical_contract=units_document([], params, implementations)['contract'])
        unit_list = document['units']
        input_provenance = copied_provenance(args.inputs_manifest, args.inputs_manifest_sha256)
    else:
        unit_list = units(params['per_cell'], params['unit_start'])
        document = units_document(unit_list, params, implementations)
        input_provenance = dict(kind='generated-in-stand', generator_numerics=NUMERICS_VERSION,
                                stand_file_sha256=file_sha256(Path(__file__)))
    by_id = {u['orbit_id']: u for u in unit_list}
    planned = plan_keys(unit_list, params)
    expected_fp = {k: job_fingerprint(by_id[k[0]], k[1], k[2], implementations)
                   for keys in planned.values() for k in keys}
    analysis_dir = (ROOT/args.analysis_dir).resolve() if args.analysis_dir else None
    out_dir = analysis_dir or run_dir
    contract = dict(version='p2-run-v1', input_contract_sha256=document['contract_sha256'],
                    jobs_sha256=digest(sorted(expected_fp.items())))
    try:
        summary = read_json(run_dir/'summary.json') if (run_dir/'summary.json').exists() else None
        if summary is not None:
            for name, value in (('params', params), ('implementations', implementations),
                                ('units_sha256', units_digest(unit_list))):
                if not isinstance(summary, dict) or summary.get(name) != value:
                    raise ValueError(f'summary.{name}: другая постановка')
        if (run_dir/'units.json').exists():
            validate_units_document(read_json(run_dir/'units.json'), document)
            if summary is not None and summary.get('units_file_sha256') not in (
                    None, file_sha256(run_dir/'units.json')):
                raise ValueError('units.json: SHA файла не совпал с summary')
        elif (args.resume or (run_dir/'run_contract.json').exists()
              or (summary and (summary.get('units_file_sha256') or summary.get('input_contract_sha256')))):
            raise ValueError('отсутствует обязательный units.json')
        local_manifest = run_dir/'inputs_manifest.json'
        if summary and summary.get('inputs_manifest_sha256'):
            if not local_manifest.exists() or file_sha256(local_manifest) != summary['inputs_manifest_sha256']:
                raise ValueError('Локальный inputs manifest утрачен или изменён')
        if local_manifest.exists():
            local_doc, _ = load_frozen_inputs(local_manifest, file_sha256(local_manifest), params=params)
            validate_units_document(local_doc, document)
        if summary and summary.get('input_contract_sha256') not in (None, document['contract_sha256']):
            raise ValueError('summary.input_contract_sha256: другая постановка')
        rows, ignored = read_rows(jobs_file, expected_fp)
        preflight(run_dir, analysis_dir, evaluate=args.evaluate_only, resume=args.resume,
                  rows=rows, expected_fp=expected_fp, contract=contract)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'ОТКАЗ СТЕНДА (ничего не записано): {exc}', flush=True)
        return 2
    source = source_manifest(run_dir) if args.evaluate_only else None
    if args.evaluate_only:
        out_dir.mkdir(parents=True, exist_ok=False)
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        if not args.resume:
            atomic_json(run_dir/'run_contract.json', contract)
    # Остановленные сроком строки при продолжении пересчитываются; при --evaluate-only учитываются.
    done = {k: r for k, r in rows.items() if args.evaluate_only or r['run_status'] != 'partial'}
    total = sum(len(k) for k in planned.values())
    print(f"{STAGE}: стенд {STAND_VERSION}; прототип v{PROTOTYPE_VERSION}; единиц {len(unit_list)} "
          f"(9 ячеек × {params['per_cell']}, j от {params['unit_start']}); горизонты {params['years']} лет, "
          f"B на {params['b_per_cell']} ед./ячейку при {params['b_years']}; заданий {total}, готово {len(done)} "
          f"(отброшено несовпавших по отпечатку {ignored}); процессов {args.workers}; срок {args.deadline_utc or 'нет'}; "
          f"объём {'полный' if full_scope else 'СОКРАЩЁННЫЙ'}; каталог {shown(run_dir)}", flush=True)
    if args.stop_file and os.path.exists(args.stop_file) and not args.evaluate_only:
        print(f'ВНИМАНИЕ: стоп-файл {args.stop_file} уже существует — задания будут сразу остановлены', flush=True)
    started = time.perf_counter()
    units_path = run_dir/'units.json'
    if not units_path.exists() and not args.evaluate_only:
        atomic_json(units_path, document)
    inputs_manifest_path = run_dir/'inputs_manifest.json'
    if not args.evaluate_only:
        if not inputs_manifest_path.exists():
            atomic_json(inputs_manifest_path, manifest_for(units_path, document, input_provenance))
        jobs = [dict(unit=by_id[o], years=y, variant=v, fingerprint=expected_fp[(o, y, v)],
                     deadline=deadline, stop_file=args.stop_file)
                for keys in planned.values() for (o, y, v) in keys if (o, y, v) not in done]
        jobs.sort(key=lambda j: -model_seconds(j['unit'], j['years'], j['variant']))
        if jobs:
            model = sum(model_seconds(j['unit'], j['years'], j['variant']) for j in jobs)
            longest = model_seconds(jobs[0]['unit'], jobs[0]['years'], jobs[0]['variant'])
            print(f'к выполнению {len(jobs)} заданий; модель плана, если все выживают: {model/3600:.2f} ядро-ч, '
                  f'самое долгое {longest/3600:.2f} ч', flush=True)
        run_jobs(jobs, args.workers, jobs_file, done, run_dir, planned, started)
    final_rows = [done[k] for keys in planned.values() for k in keys if k in done]
    analysis = analyze(final_rows, unit_list, params, full_scope)
    report(analysis)
    stopped = sum(1 for r in final_rows if r['run_status'] == 'partial')
    failed = sum(1 for r in final_rows if r['run_status'] == 'failed')
    atomic_json(out_dir/'analysis.json', analysis, replace=args.resume)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    atomic_json(out_dir/'summary.json', dict(plan=PLAN, stage=STAGE, utc=stamp,
        stand_version=STAND_VERSION, prototype_version=PROTOTYPE_VERSION, machine=cpu_info(),
        params=params, full_scope=full_scope, workers=args.workers, deadline_utc=args.deadline_utc,
        stop_file=args.stop_file, evaluate_only=args.evaluate_only, units=len(unit_list),
        units_sha256=units_digest(unit_list), generator_numerics=document['generator_numerics'],
        input_basis='frozen' if args.inputs_manifest else 'generator',
        inputs_manifest_sha256=file_sha256(inputs_manifest_path) if inputs_manifest_path.exists() else None,
        frozen_anchor=args.inputs_manifest_sha256,
        input_contract_sha256=document['contract_sha256'], source=source,
        units_file_sha256=file_sha256(units_path) if units_path.exists() else None, planned=total, done=len(final_rows), stopped=stopped,
        failed=failed, complete=analysis['complete'], decision=analysis['decision'],
        implementations=implementations, wall_seconds=time.perf_counter()-started), replace=args.resume)
    if not args.evaluate_only and analysis['complete']:
        atomic_json(run_dir/'sealed.json', dict(version=1, utc=stamp, scientific_admission=False,
                                               summary_sha256=file_sha256(out_dir/'summary.json')))
    ok = analysis['complete'] and not failed
    b_left = sum(level['b_unfinished'] for level in analysis['horizons'].values())
    print(f"\nИТОГ СТЕНДА: {'все задания завершены' if ok else 'НЕ ЗАВЕРШЕНО или есть отказы'} "
          f"({len(final_rows)}/{total}, остановлено {stopped}, отказов {failed}; "
          f"D/T на всех горизонтах {'завершены' if all(lv['dt_complete'] for lv in analysis['horizons'].values()) else 'НЕ завершены'}, "
          f"B не завершено {b_left}); каталог {shown(run_dir)}")
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
