"""W2-P015 (Фаза 3, W2-T061): адресная сходимость исходов и времён событий.

Режимы
  freeze     (до расчётов)  выбор контролей по данным B144/P2-2a и состав набора ->
                            data/manifests/W2_phase3_cases_v1.json (не перезаписывается)
  prepare    (WSL)          точные старты: units.json B144 или регенерация со сверкой всех архивных
                            job fingerprint P2-2a и стартовой пробы; при расхождении расчёта нет (exit 3);
                            run-каталог блока P0 | P1 | perturb: units.json, identity.json, manifest.json
  benchmark  (WSL)          все ступени P0/P1 на коротком горизонте (scratch) -> прогноз wall блоков на
                            этом ПК и правило B-горизонта ganymede:canonical_volume:0126
  run        (WSL)          задания блока, долгие первыми, 1 CPU на worker, deadline и stop-file;
                            jobs.jsonl дописывается, плотные траектории — samples/<fp>.npz с SHA
  analyze    (локально)     классы К1–К4, таблицы пар, правило режима §7 (convergence.py)

Постановка, пороги и правила заморожены до первого нового исхода: configs/experiments/
W2_phase3_convergence_v1.yaml и src/submoon_research/diagnostics/convergence.py (W2-D046).
Код движков не меняется: D/T — integrate_compiled, B — integrate_engine('ias15_reboundx') с той же
обёрткой Monitor, что B144 (диагностика вокруг шага, численный путь прежний). Выборки берутся из
dense output и не меняют шаги (тест test_phase3_convergence). Разработочный адресный набор:
не допуск и не доля ошибок ансамбля.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import heapq
import io
import json
import math
import multiprocessing as mp
import os
import platform
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ.setdefault(_name, '1')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402
import yaml  # noqa: E402

from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, canonical, digest, file_digest, loads, read_json, units_digest,
)
from submoon_research.diagnostics import convergence as cv  # noqa: E402

CONFIG = ROOT/'configs/experiments/W2_phase3_convergence_v1.yaml'
CASES = ROOT/'data/manifests/W2_phase3_cases_v1.json'
STAND = 'W2-PH3-v1'
YEAR = cv.YEAR
BLOCKS = ('P0', 'P1', 'perturb')
B_FALLBACK_SECONDS = 2*3600.0
CODE = ('scripts/phase3_convergence.py', 'src/submoon_research/diagnostics/convergence.py',
        'scripts/b144_regress.py', 'scripts/admission_p2_2.py', 'scripts/admission_p2_horizons.py',
        'src/submoon_research/dynamics/engine_compare.py', 'src/submoon_research/dynamics/native_ias15.py',
        'src/submoon_research/dynamics/compiled_dop853.py', 'src/submoon_research/dynamics/dense_segments.py',
        'src/submoon_research/dynamics/fast_forces.py', 'src/submoon_research/events/dense_contact.py',
        'src/submoon_research/events/escape.py', 'src/submoon_research/exact_linalg.py',
        'configs/experiments/W2_phase3_convergence_v1.yaml', 'requirements-w2.txt')


def utc():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def rel(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def code_hashes():
    return {name: file_digest(ROOT/name) for name in CODE if (ROOT/name).exists()}


def environment():
    import importlib.metadata as metadata
    packages = {}
    for name in ('numpy', 'scipy', 'numba', 'rebound', 'reboundx'):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    text = ''
    with contextlib.suppress(OSError):
        text = Path('/proc/cpuinfo').read_text()
    model = next((line.split(':', 1)[1].strip() for line in text.splitlines()
                  if line.startswith('model name')), platform.processor())
    return dict(python=platform.python_version(), platform=platform.platform(), cpu_model=model,
                packages=packages, cpu_count=os.cpu_count(),
                openblas_coretype=os.environ.get('OPENBLAS_CORETYPE'))


# ---------------------------------------------------------------- конфигурация и архивы

def load_config(path=CONFIG):
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if cfg.get('schema') != 'w2-phase3-convergence-v1':
        raise ValueError('Неожиданная схема конфигурации')
    for code, spec in cfg['modes'].items():
        if spec['family'] not in cfg['ladders'] or code not in cfg['ladders'][spec['family']]:
            raise ValueError('Ступень вне лестницы: '+code)
    return cfg


def mode_fields(cfg, code):
    spec = dict(cfg['modes'][code])
    return spec.pop('family'), {k: float(v) for k, v in spec.items()}


def numeric_mode(cfg, code):
    from submoon_research.dynamics.engine_compare import NumericMode
    family, fields = mode_fields(cfg, code)
    return family, NumericMode(**fields)


def _jsonl(path, sha):
    if file_digest(path) != sha:
        raise ValueError('Изменился закреплённый архив '+rel(path))
    return [loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip()]


def archives(cfg, root=ROOT):
    """P2-2a: (orbit, years, variant) -> строка; B144: orbit -> строка нового B (1000 лет), units."""
    a, b = cfg['archives']['p2_2a'], cfg['archives']['b144']
    if file_digest(root/a['run']/'summary.json') != a['summary_sha256']:
        raise ValueError('Изменился summary P2-2a')
    p2 = {(r['orbit_id'], r['years'], r['variant']): r for r in _jsonl(root/a['run']/'jobs.jsonl', a['jobs_sha256'])}
    if file_digest(root/b['run']/'manifest.json') != b['manifest_sha256']:
        raise ValueError('Изменился manifest B144')
    if file_digest(root/b['run']/'units.json') != b['units_file_sha256']:
        raise ValueError('Изменился units.json B144')
    b_rows = {r['orbit_id']: r for r in _jsonl(root/b['run']/'jobs.jsonl', b['jobs_sha256']) if r['years'] == 1000.0}
    units = {u['orbit_id']: u for u in read_json(root/b['run']/'units.json')['units']}
    summary = read_json(root/a['run']/'summary.json')
    return dict(p2=p2, b144=b_rows, units=units, implementations=summary['implementations'],
                generator_numerics=summary.get('generator_numerics'))


def _context(oid, arch, years=1000.0):
    out = {}
    for v in ('D', 'T', 'B'):
        r = arch['p2'].get((oid, years, v))
        if r is not None:
            out['p2_2a_'+v] = dict(label=cv.label(r), time_years=cv.outcome_time(r)/YEAR, wall=r['wall'])
    r = arch['b144'].get(oid)
    if r is not None:
        out['b144_new_B'] = dict(label=cv.label(r), time_years=cv.outcome_time(r)/YEAR, wall=r['wall'])
    return out


# ---------------------------------------------------------------- freeze

def freeze(cfg, out=CASES, root=ROOT, config_path=CONFIG):
    out = Path(out)
    if out.exists():
        raise SystemExit('набор уже заморожен; не перезаписывается')
    arch = archives(cfg, root)
    targets = [t['orbit_id'] for t in cfg['targets']]
    deferred = [o for group in cfg['deferred'].values() for o in group]
    if set(targets) & set(deferred):
        raise ValueError('Цель среди отложенных')
    periods = {o: u['period_seconds'] for o, u in arch['units'].items()}
    by = {v: {o: r for (o, y, vv), r in arch['p2'].items() if y == 1000.0 and vv == v} for v in ('D', 'T')}
    sel = cv.select_controls(arch['b144'], by['D'], by['T'], periods, set(targets) | set(deferred),
                             float(cfg['controls']['cost_cap_b_wall_seconds']))
    perturbed = cv.perturbed_controls(sel['chosen'], arch['b144'])
    cases = []
    for t in cfg['targets']:
        cases.append(dict(t, role='target', source='b144_units' if t['orbit_id'] in arch['units'] else 'regenerate',
                          context=_context(t['orbit_id'], arch)))
    for group, oid in sel['chosen'].items():
        cases.append(dict(orbit_id=oid, block='P1', role='control', control_group=group, high_e=False,
                          codes=list(cfg['controls']['codes']),
                          perturb_family='Ts' if oid in perturbed.values() else None,
                          source='b144_units', context=_context(oid, arch)))
    doc = dict(schema='w2-phase3-cases-v1', task=cfg['task'], plan=cfg['plan'], decision=cfg['decision'],
               created_utc=utc(), config_sha256=file_digest(config_path), rules_version=cv.RULES_VERSION,
               convergence_sha256=file_digest(root/'src/submoon_research/diagnostics/convergence.py'),
               controls=dict(sel, perturbed=perturbed, rule=cfg['controls']), deferred=cfg['deferred'],
               cases=cases)
    atomic_json(out, doc)
    print('cases', file_digest(out), len(cases), 'cases;', len(sel['chosen']), 'controls', flush=True)
    return doc


def load_cases(sha, cfg_path=CONFIG, path=CASES):
    if not sha or file_digest(path) != sha:
        raise ValueError('SHA набора не совпадает с закреплённым')
    doc = read_json(path)
    if doc['config_sha256'] != file_digest(cfg_path):
        raise ValueError('Конфигурация изменилась после freeze')
    return doc


# ---------------------------------------------------------------- точные старты

def check_unit(unit, rows, implementations, job_fingerprint):
    """Все архивные задания единицы: полный job fingerprint и стартовая проба."""
    bad = []
    for r in rows:
        fp = job_fingerprint(unit, r['years'], r['variant'], implementations) == r['job_fingerprint']
        probe = canonical(list(unit['state'][-6:])) == canonical(list(r['samples'][0][1]))
        if not (fp and probe):
            bad.append(dict(years=r['years'], variant=r['variant'], job_fingerprint=fp, initial_probe=probe))
    return bad


def resolve_units(wanted, cfg, root=ROOT):
    arch = archives(cfg, root)
    units, identity = {}, []
    regen = []
    for oid in wanted:
        if oid in arch['units']:
            units[oid] = arch['units'][oid]
            identity.append(dict(orbit_id=oid, source='b144_units', passed=True))
        else:
            regen.append(oid)
    if regen:
        import admission_p2_2 as stand
        from submoon_research.exact_linalg import LEGACY, numerics
        with numerics(arch['generator_numerics'] or LEGACY):
            prepared = stand.setups(root)
            for oid in regen:
                host, measure, j = oid.split(':')
                unit = next(u for u in stand.units(1, int(j), prepared) if u['orbit_id'] == oid)
                rows = [r for (o, _, _), r in arch['p2'].items() if o == oid]
                bad = check_unit(unit, rows, arch['implementations'], stand.job_fingerprint) if rows else ['no rows']
                identity.append(dict(orbit_id=oid, source='regenerate', passed=not bad, checked=len(rows),
                                     mismatches=bad))
                if not bad:
                    units[oid] = unit
    return units, identity


# ---------------------------------------------------------------- задания

def job_grid(horizon_s, p0, window_years, grid_cfg):
    """0 ∪ geomspace(log_start·H, H, log_points) ∪ arange(0, окно, dense_step·P0); маска полного состояния."""
    log = np.geomspace(float(grid_cfg['log_start_fraction'])*horizon_s, horizon_s, int(grid_cfg['log_points']))
    parts = [np.array([0.0]), log]
    if window_years and window_years > 0:
        step = float(grid_cfg['dense_step_p0'])*p0
        end = min(float(window_years)*YEAR, horizon_s, step*int(grid_cfg['dense_max_points']))
        parts.append(np.arange(0.0, end, step))
    times = np.unique(np.concatenate(parts))
    full = np.isin(times, np.concatenate([[0.0], log]))
    return times, full


def plan_jobs(block, cases, cfg, b_years=None):
    """Состав блока. b_years: orbit -> горизонт B-ступеней (правило fallback из бенчмарка)."""
    if block not in BLOCKS:
        raise ValueError('Неизвестный блок '+str(block))
    b_years = b_years or {}
    horizon = float(cfg['horizon_years'])
    out = []
    for c in cases:
        if block in ('P0', 'P1') and c['block'] == block:
            window = c.get('dense_window_years', cfg['dense_window_years_default'])
            for code in c['codes']:
                family = cfg['modes'][code]['family']
                years = float(b_years.get(c['orbit_id'], horizon)) if family == 'ias15' else horizon
                out.append(dict(orbit_id=c['orbit_id'], code=code, years=years, perturbation=None,
                                dense_window_years=float(window)))
        if block == 'perturb' and c.get('perturb_family'):
            p = cfg['perturbation']
            window = c.get('dense_window_years', cfg['dense_window_years_default']) \
                if cfg['grid'].get('perturbation_dense') else 0.0
            for scale in p['scales']:
                for direction in p['directions']:
                    out.append(dict(orbit_id=c['orbit_id'], code=c['perturb_family'],
                                    years=float(p['horizon_years']), dense_window_years=float(window),
                                    perturbation=dict(direction=direction, scale=float(scale),
                                                      seed=int(p['isotropic_seed']))))
    return out


def job_fingerprint(spec, unit, cfg, implementations):
    family, fields = mode_fields(cfg, spec['code'])
    return digest(dict(stand=STAND, orbit_id=spec['orbit_id'], unit=digest(unit), code=spec['code'],
                       mode=fields, family=family, years=spec['years'], perturbation=spec['perturbation'],
                       window=spec['dense_window_years'], grid=cfg['grid'],
                       implementation=implementations[family]))


def current_implementations():
    from submoon_research.dynamics import engine_compare
    from submoon_research.dynamics.compiled_dop853 import implementation_fingerprint
    return dict(dop853=implementation_fingerprint(), ias15=engine_compare.b_implementation_fingerprint())


def b_fallback(cases, benchmark):
    """Правило budget.b_fallback_rule: прогноз B на полные 1000 лет (без учёта раннего события)
    > 2 ч на этом ПК -> b_horizon_fallback_years."""
    out = {}
    pred = benchmark.get('predicted_full_horizon_seconds', {}) if benchmark else {}
    for c in cases:
        years = c.get('b_horizon_fallback_years')
        if years is None:
            continue
        worst = max((v for k, v in pred.items() if k.startswith(c['orbit_id']+'|B')), default=None)
        if worst is not None and worst > B_FALLBACK_SECONDS:
            out[c['orbit_id']] = float(years)
    return out


# ---------------------------------------------------------------- исполнение одного задания

def _worker_init():
    import b144_regress as b144
    b144._worker_init()


def _save_samples(path, **arrays):
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    data = buffer.getvalue()
    tmp = path.with_name('.'+path.name+'.part')
    tmp.write_bytes(data)
    os.replace(tmp, path)
    import hashlib
    return hashlib.sha256(data).hexdigest()


def run_job(job):
    import b144_regress as b144
    from admission_p2_horizons import Budget, unresolved_reason
    from submoon_research.dynamics.compiled_dop853 import integrate_compiled
    from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine

    unit, cfg = job['unit'], job['config']
    family, fields = mode_fields(cfg, job['code'])
    p0 = float(unit['period_seconds'])
    state = list(unit['state'])
    if job['perturbation']:
        p = job['perturbation']
        state[-6:] = cv.perturbation(state[-6:], float(unit['a_km']), 2*math.pi/p0, p['direction'], p['scale'], p['seed'])
    horizon = float(job['years'])*YEAR
    times, full = job_grid(horizon, p0, job['dense_window_years'], cfg['grid'])
    kwargs = dict(period=p0, mode=NumericMode(**fields), hill_km=unit['hill_km'], escape_window=unit['escape_window'],
                  times=times, budget=Budget(job.get('deadline'), job.get('stop_file')))
    base = dict(stand=STAND, orbit_id=unit['orbit_id'], host=unit['host'], measure=unit['measure'],
                unit=unit['unit'], code=job['code'], family=family, years=job['years'],
                perturbation=job['perturbation'], dense_window_years=job['dense_window_years'],
                job_fingerprint=job['fingerprint'], period_seconds=p0, a_km=unit['a_km'])
    started, cpu = time.perf_counter(), time.process_time()
    try:
        if family == 'ias15':
            b144.Monitor.reset()
            out = integrate_engine('ias15_reboundx', state, unit['gms'], unit['figures'], unit['radii'],
                                   horizon, **kwargs)
        else:
            out = integrate_compiled(state, unit['gms'], unit['figures'], unit['radii'], horizon, **kwargs)
    except Exception as exc:  # noqa: BLE001 — сбой задания записывается, блок продолжается
        return dict(base, wall=time.perf_counter()-started, cpu=time.process_time()-cpu, run_status='failed',
                    error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc(), outcome='unresolved',
                    unresolved_reason='computational', last_valid_time=0.0, event=None)
    wall, cpu = time.perf_counter()-started, time.process_time()-cpu
    st = np.array([s['time'] for s in out['samples']], dtype=float)
    states = np.array([s['state'] for s in out['samples']], dtype=float).reshape(len(st), -1)
    mask = full[:len(st)] if len(st) <= len(full) else np.ones(len(st), bool)
    path = Path(job['run_dir'])/'samples'/(job['fingerprint'][:24]+'.npz')
    sha = _save_samples(path, times=st, probe=states[:, -6:], full_times=st[mask], full_state=states[mask])
    event = out['event']
    row = dict(base, wall=wall, cpu=cpu, run_status=out['run_status'], error=out['error'],
               outcome=out['physical_outcome'], unresolved_reason=unresolved_reason(out),
               last_valid_time=out['last_valid_time'],
               event=None if event is None else dict(event=event['event'], time=event['time'],
                                                     body=event.get('body_index')),
               steps=out['steps'], nfev=out['nfev'], endpoint_residual=out.get('endpoint_residual'),
               massive_energy_relative_drift=out.get('massive_energy_relative_drift'),
               eccentricity=out.get('eccentricity_monitor'), numerical_domain=out.get('numerical_domain'),
               final_state=out['final_state'], checkpoint=out.get('checkpoint'),
               samples_file=rel(path), samples_sha256=sha, samples_count=int(len(st)),
               grid_diagnostics=cv.grid_diagnostics(st, states[:, -6:], unit['gms'][0], unit['hill_km'])
               if len(st) else None,
               b_diagnostics=dict(b144.Monitor.stats) if family == 'ias15' else None,
               runner=dict(task='W2-T061', pid=os.getpid(), finished_utc=utc()))
    return row


def pool_rows(jobs, workers):
    with contextlib.ExitStack() as stack:
        if workers > 0:
            pool = stack.enter_context(mp.get_context('spawn').Pool(workers, initializer=_worker_init))
            yield from pool.imap_unordered(run_job, jobs, chunksize=1)
        else:
            _worker_init()
            yield from map(run_job, jobs)


# ---------------------------------------------------------------- prepare / run

def prepare(run_dir, block, cases_sha, benchmark_path=None, expected_b=None):
    cfg = load_config()
    cases_doc = load_cases(cases_sha)
    benchmark = read_json(benchmark_path) if benchmark_path else None
    b_years = b_fallback(cases_doc['cases'], benchmark) if block in ('P0', 'P1') else {}
    specs = plan_jobs(block, cases_doc['cases'], cfg, b_years)
    if not specs:
        raise SystemExit('пустой блок')
    impl = current_implementations()
    expected_b = expected_b or cfg['expected_b_implementation']
    if impl['ias15'] != expected_b:
        print('B implementation differs from the tested version:', impl['ias15'], flush=True)
        return 5
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    units, identity = resolve_units(sorted({s['orbit_id'] for s in specs}), cfg)
    atomic_json(run_dir/'identity.json', dict(passed=all(i['passed'] for i in identity), utc=utc(),
                                              environment=environment(), units=identity))
    missing = sorted({s['orbit_id'] for s in specs} - set(units))
    if missing:
        print('IDENTITY MISMATCH:', missing, '— nothing integrated', flush=True)
        return 3
    plan = [dict(s, fingerprint=job_fingerprint(s, units[s['orbit_id']], cfg, impl)) for s in specs]
    unit_list = [units[o] for o in sorted(units)]
    atomic_json(run_dir/'units.json', dict(schema='w2-phase3-units-v1', units_sha256=units_digest(unit_list),
                                           units=unit_list))
    manifest = dict(schema='w2-phase3-manifest-v1', task='W2-T061', plan_id=cfg['plan'], block=block,
                    created_utc=utc(), stand=STAND, rule=__doc__, cases_sha256=cases_sha,
                    config_sha256=file_digest(CONFIG), rules_version=cv.RULES_VERSION,
                    units_file_sha256=file_digest(run_dir/'units.json'), implementations=impl,
                    b_horizon_years=b_years,
                    benchmark=dict(path=rel(benchmark_path), sha256=file_digest(benchmark_path)) if benchmark_path else None,
                    plan=plan, code_sha256=code_hashes(), environment=environment())
    atomic_json(run_dir/'manifest.json', manifest)
    print('manifest', file_digest(run_dir/'manifest.json'), 'block', block, 'jobs', len(plan), flush=True)
    return 0


def load_manifest(run_dir, sha):
    run_dir = Path(run_dir)
    if not sha or file_digest(run_dir/'manifest.json') != sha:
        raise ValueError('SHA manifest не совпадает')
    manifest = read_json(run_dir/'manifest.json')
    if file_digest(run_dir/'units.json') != manifest['units_file_sha256']:
        raise ValueError('units.json изменился после manifest')
    return manifest


def read_rows(run_dir):
    path = Path(run_dir)/'jobs.jsonl'
    rows = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if line.strip():
                r = loads(line)
                rows[r['job_fingerprint']] = r  # последняя строка задания (partial пересчитывается)
    return rows


def order_by_cost(plan, estimate):
    return sorted(plan, key=lambda p: (-estimate.get(p['fingerprint'], 0.0), p['fingerprint']))


def run(run_dir, manifest_sha, workers, deadline_utc=None, stop_file=None, estimate=None):
    from admission_p2_horizons import parse_deadline
    run_dir = Path(run_dir)
    manifest = load_manifest(run_dir, manifest_sha)
    if manifest['implementations'] != current_implementations():
        print('Implementation changed after prepare; nothing integrated', flush=True)
        return 5
    cfg = load_config()
    units = {u['orbit_id']: u for u in read_json(run_dir/'units.json')['units']}
    done = {fp for fp, r in read_rows(run_dir).items() if r['run_status'] in ('completed', 'failed')}
    deadline = parse_deadline(deadline_utc)
    todo = [p for p in order_by_cost(manifest['plan'], estimate or {}) if p['fingerprint'] not in done]
    jobs = [dict(p, unit=units[p['orbit_id']], config=cfg, deadline=deadline, stop_file=stop_file,
                 run_dir=str(run_dir)) for p in todo]
    print(f'{utc()} block {manifest["block"]}: {len(jobs)} of {len(manifest["plan"])} jobs to run, '
          f'workers {workers}', flush=True)
    started = time.perf_counter()
    counts = {}
    with (run_dir/'jobs.jsonl').open('a', encoding='utf-8') as stream:
        for k, row in enumerate(pool_rows(jobs, workers), 1):
            stream.write(json.dumps(row, ensure_ascii=False, default=float)+'\n')
            stream.flush()
            counts[row['run_status']] = counts.get(row['run_status'], 0)+1
            tmp = run_dir/'.progress.json.part'
            tmp.write_text(json.dumps(dict(updated_utc=utc(), done=k, todo=len(jobs), counts=counts,
                                           elapsed_s=time.perf_counter()-started,
                                           last=dict(orbit_id=row['orbit_id'], code=row['code'],
                                                     status=row['run_status'], wall=row['wall'])),
                                      ensure_ascii=False, indent=1), encoding='utf-8')
            os.replace(tmp, run_dir/'progress.json')
            print(f'  {k}/{len(jobs)} {row["orbit_id"]} {row["code"]} {row["run_status"]} '
                  f'{cv.label(row)} wall {row["wall"]:.1f}s', flush=True)
    print(f'{utc()} done {counts} in {time.perf_counter()-started:.0f} s', flush=True)
    return 0 if counts.get('partial', 0) == 0 else 6


# ---------------------------------------------------------------- benchmark

def lpt_wall(durations, workers):
    """Длительность расписания «самые долгие первыми» на workers процессах."""
    loads_ = [0.0]*max(1, workers)
    heapq.heapify(loads_)
    for d in sorted(durations, reverse=True):
        heapq.heappush(loads_, heapq.heappop(loads_)+d)
    return max(loads_)


def predict_seconds(row, case, cfg):
    """Экстраполяция короткого задания на полный горизонт: ∝ времени до архивного исхода ближайшего варианта."""
    if row['run_status'] == 'completed' and (row.get('event') or row['outcome'] == 'unresolved'):
        return row['wall']  # закончилось раньше короткого горизонта
    family = cfg['modes'][row['code']]['family']
    ctx = case.get('context', {})
    ref = (ctx.get('b144_new_B') or ctx.get('p2_2a_B') or ctx.get('p2_2a_T')) if family == 'ias15' \
        else (ctx.get('p2_2a_D') if row['code'] in ('D', 'Ds') else ctx.get('p2_2a_T'))
    full = float(cfg['horizon_years'])
    t_end = min(full, ref['time_years']) if ref else full
    return row['wall']*max(t_end, row['years'])/row['years']


def benchmark(run_dir, cases_sha, years, workers, deadline_utc):
    from admission_p2_horizons import parse_deadline
    cfg = load_config()
    cases_doc = load_cases(cases_sha)
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    by_id = {c['orbit_id']: c for c in cases_doc['cases']}
    specs = [dict(s, years=float(years)) for b in ('P0', 'P1') for s in plan_jobs(b, cases_doc['cases'], cfg)]
    units, identity = resolve_units(sorted({s['orbit_id'] for s in specs}), cfg)
    atomic_json(run_dir/'identity.json', dict(passed=all(i['passed'] for i in identity), units=identity))
    specs = [s for s in specs if s['orbit_id'] in units]
    impl = current_implementations()
    jobs = [dict(s, fingerprint=job_fingerprint(s, units[s['orbit_id']], cfg, impl), unit=units[s['orbit_id']],
                 config=cfg, deadline=parse_deadline(deadline_utc), stop_file=None, run_dir=str(run_dir))
            for s in specs]
    rows = []
    with (run_dir/'jobs.jsonl').open('a', encoding='utf-8') as stream:
        for row in pool_rows(jobs, workers):
            stream.write(json.dumps(row, ensure_ascii=False, default=float)+'\n')
            rows.append(row)
    pred, full, partial = {}, {}, []
    for r in rows:
        key = f"{r['orbit_id']}|{r['code']}"
        if r['run_status'] == 'partial':
            partial.append(key)
            continue
        pred[key] = predict_seconds(r, by_id[r['orbit_id']], cfg)
        early = r['run_status'] == 'completed' and (r.get('event') or r['outcome'] == 'unresolved')
        full[key] = r['wall'] if early else r['wall']*float(cfg['horizon_years'])/r['years']
    blocks = {}
    for b in ('P0', 'P1'):
        d = [v for k, v in pred.items() if by_id[k.split('|')[0]]['block'] == b]
        blocks[b] = dict(jobs=len(d), cpu_s=sum(d), wall_s=lpt_wall(d, workers))
    pert = []
    for c in cases_doc['cases']:
        fam = c.get('perturb_family')
        if fam and f"{c['orbit_id']}|{fam}" in pred:
            pert += [pred[f"{c['orbit_id']}|{fam}"]]*len(cfg['perturbation']['scales'])*len(cfg['perturbation']['directions'])
    blocks['perturb'] = dict(jobs=len(pert), cpu_s=sum(pert), wall_s=lpt_wall(pert, workers))
    fallback = b_fallback(cases_doc['cases'], dict(predicted_full_horizon_seconds=full))
    total = sum(v['wall_s'] for v in blocks.values())
    doc = dict(schema='w2-phase3-benchmark-v1', utc=utc(), years=float(years), workers=workers,
               environment=environment(), cases_sha256=cases_sha, predicted_seconds=pred,
               predicted_full_horizon_seconds=full, partial=partial,
               blocks=blocks, total_wall_s=total, b_fallback=fallback,
               limits=dict(block_wall_hours_max=cfg['budget']['block_wall_hours_max'],
                           total_hours_warn=cfg['budget']['total_hours_warn']),
               note='модельная экстраполяция короткого горизонта; не измерение полного расчёта')
    atomic_json(run_dir/'benchmark.json', doc)
    for b, v in blocks.items():
        print(f'{b}: {v["jobs"]} jobs, CPU {v["cpu_s"]/3600:.2f} h, wall {v["wall_s"]/3600:.2f} h on {workers}')
    print(f'total wall {total/3600:.2f} h; B fallback {fallback}; partial {partial}', flush=True)
    return 0


# ---------------------------------------------------------------- analyze

def _dense(row):
    path = ROOT/row['samples_file']
    data = path.read_bytes()
    import hashlib
    if hashlib.sha256(data).hexdigest() != row['samples_sha256']:
        raise ValueError('SHA выборок не совпадает: '+row['samples_file'])
    with np.load(io.BytesIO(data)) as z:
        return z['times'], z['probe']


def pair_table(rows, dense, a0, n0, p0, thresholds):
    out = []
    codes = sorted(rows)
    for i, a in enumerate(codes):
        for b in codes[i+1:]:
            item = dict(pair=[a, b], labels=[cv.label(rows[a]), cv.label(rows[b])],
                        dt_p0=abs(cv.outcome_time(rows[a])-cv.outcome_time(rows[b]))/p0)
            if dense.get(a) is not None and dense.get(b) is not None:
                t, d = cv.divergence(*dense[a], *dense[b], a0, n0)
                item.update(tau=cv.first_exceedance(t, d, thresholds), growth=cv.growth_fit(t, d),
                            max_delta=float(d.max()) if d.size else None, common_points=int(t.size))
            out.append(item)
    return out


def analyze(run_dirs, output, cases_sha):
    cfg = load_config()
    cases_doc = load_cases(cases_sha)
    output = Path(output)
    if output.exists():
        raise SystemExit('выход уже существует; не перезаписывается')
    thr = cfg['thresholds']
    planned, rows, units, manifests = {}, {}, {}, []
    for d in run_dirs:
        d = Path(d)
        m = read_json(d/'manifest.json')
        if m['cases_sha256'] != cases_sha or m['config_sha256'] != file_digest(CONFIG):
            raise ValueError('run относится к другому набору/конфигурации: '+rel(d))
        manifests.append(dict(run=rel(d), block=m['block'], manifest_sha256=file_digest(d/'manifest.json')))
        for p in m['plan']:
            planned[p['fingerprint']] = p
        rows.update({fp: r for fp, r in read_rows(d).items() if fp in planned})
        units.update({u['orbit_id']: u for u in read_json(d/'units.json')['units']})
    arch = archives(cfg)
    scales = [float(s) for s in cfg['perturbation']['scales']]
    results, controls, b_checks, targets_for_rule = [], [], [], []
    for c in cases_doc['cases']:
        oid = c['orbit_id']
        mine = [p for p in planned.values() if p['orbit_id'] == oid]
        if not mine or oid not in units:
            results.append(dict(orbit_id=oid, role=c['role'], status='not_run'))
            continue
        u = units[oid]
        p0, a0 = float(u['period_seconds']), float(u['a_km'])
        n0 = 2*math.pi/p0
        ladder = {p['code']: rows[p['fingerprint']] for p in mine if p['perturbation'] is None and p['fingerprint'] in rows}
        ladder = {k: r for k, r in ladder.items() if r['run_status'] == 'completed'}
        prow = [rows[p['fingerprint']] for p in mine if p['perturbation'] and p['fingerprint'] in rows]
        missing = sorted(p['code'] for p in mine if p['perturbation'] is None and p['code'] not in ladder)
        dense = {k: _dense(r) for k, r in ladder.items()}
        item = dict(orbit_id=oid, role=c['role'], block=c['block'], high_e=c['high_e'], p0_seconds=p0,
                    missing_or_incomplete=missing,
                    labels={k: cv.label(r) for k, r in ladder.items()},
                    times_years={k: cv.outcome_time(r)/YEAR for k, r in ladder.items()},
                    pairs=pair_table(ladder, dense, a0, n0, p0, thr['divergence']),
                    archive=c.get('context'),
                    platform=_platform(oid, ladder, arch, p0))
        case = dict(rows=ladder, dense=dense, ladders=cfg['ladders'], a0=a0, n0=n0, p0=p0, high_e=c['high_e'],
                    perturb_family=c.get('perturb_family'), perturb_rows=prow, scales=scales)
        item['classification'] = cv.classify(case, thr) if not missing else dict(cls=None, reason='неполная лестница')
        results.append(item)
        if c['role'] == 'target':
            cls = item['classification']
            targets_for_rule.append(dict(orbit_id=oid, high_e=c['high_e'], cls=cls.get('cls'), wrong=cls.get('wrong')))
            if c['high_e']:
                b_checks.append(dict(orbit_id=oid, labels={k: item['labels'][k] for k in ('B', 'Bs', 'Be')
                                                           if k in item['labels']}))
        else:
            controls.append(dict(orbit_id=oid, labels=item['labels']))
    decision = cv.decide_regime(targets_for_rule, controls, b_checks)
    output.mkdir(parents=True, exist_ok=False)
    doc = dict(schema='w2-phase3-analysis-v1', task='W2-T061', utc=utc(), cases_sha256=cases_sha,
               config_sha256=file_digest(CONFIG), rules_version=cv.RULES_VERSION, runs=manifests,
               planned_jobs=len(planned), rows=len(rows), cases=results, decision=decision,
               note='разработочный адресный набор; не допуск и не доля ошибок ансамбля')
    atomic_json(output/'analysis.json', doc)
    with (output/'cases.csv').open('w', encoding='utf-8', newline='') as stream:
        w = csv.writer(stream)
        w.writerow(['orbit_id', 'role', 'block', 'high_e', 'class', 'confidence', 'labels', 'times_years'])
        for r in results:
            cls = r.get('classification') or {}
            w.writerow([r['orbit_id'], r['role'], r.get('block'), r.get('high_e'), cls.get('cls'),
                        cls.get('confidence'), json.dumps(r.get('labels'), ensure_ascii=False),
                        json.dumps({k: round(v, 6) for k, v in (r.get('times_years') or {}).items()})])
    print(json.dumps(decision, ensure_ascii=False, indent=1))
    return 0


def _platform(oid, ladder, arch, p0):
    """Воспроизведение архивных D/T (P2-2a) и нового B (B144) на этом ПК — диагностика платформы."""
    out = {}
    pairs = dict(D=arch['p2'].get((oid, 1000.0, 'D')), T=arch['p2'].get((oid, 1000.0, 'T')), B=arch['b144'].get(oid))
    for code, old in pairs.items():
        new = ladder.get(code)
        if old is None or new is None or new['years'] != 1000.0:
            continue
        out[code] = dict(same_label=cv.label(old) == cv.label(new),
                         bitwise_time=cv.outcome_time(old) == cv.outcome_time(new),
                         dt_p0=abs(cv.outcome_time(old)-cv.outcome_time(new))/p0)
    return out


# ---------------------------------------------------------------- CLI

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('mode', choices=('freeze', 'prepare', 'benchmark', 'run', 'analyze'))
    p.add_argument('--run-dir')
    p.add_argument('--runs', nargs='*')
    p.add_argument('--block', choices=BLOCKS)
    p.add_argument('--cases-sha256')
    p.add_argument('--manifest-sha256')
    p.add_argument('--benchmark')
    p.add_argument('--years', type=float, default=10.0)
    p.add_argument('--workers', type=int, default=1)
    p.add_argument('--deadline-utc')
    p.add_argument('--stop-file')
    p.add_argument('--output')
    a = p.parse_args(argv)
    if a.mode == 'freeze':
        freeze(load_config())
        return 0
    if a.mode == 'prepare':
        return prepare(a.run_dir, a.block, a.cases_sha256, a.benchmark)
    if a.mode == 'benchmark':
        return benchmark(a.run_dir, a.cases_sha256, a.years, a.workers, a.deadline_utc)
    if a.mode == 'run':
        estimate = None
        if a.benchmark:
            pred = read_json(a.benchmark)['predicted_seconds']
            m = read_json(Path(a.run_dir)/'manifest.json')
            estimate = {q['fingerprint']: pred.get(f"{q['orbit_id']}|{q['code']}", 0.0) for q in m['plan']}
        return run(a.run_dir, a.manifest_sha256, a.workers, a.deadline_utc, a.stop_file, estimate)
    return analyze(a.runs or [], a.output, a.cases_sha256)


if __name__ == '__main__':
    raise SystemExit(main())
