"""Оркестратор с общим дедлайном, отдельными runs и ограниченным пулом процессов."""
import argparse
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import uuid

import numpy as np
import psutil

from submoon_research.dynamics.engine_compare import ENGINES, NumericMode
from submoon_research.dynamics.native_ias15 import MassiveCache
from submoon_research.execution import RunContext
from submoon_research.tracking import utc_now
from submoon_research.workflows.smoke import write_json, sha256, environment
from submoon_research.workflows.w2_comparison import (
    load_cases, performance_cases, run_case, warmup, analytical_controls,
    compare_rows, profile_original,
)


def worker_init():
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
        os.environ[name] = '1'
    warmup()


def machine_info():
    info = dict(platform=platform.platform(), logical_cpus=os.cpu_count(),
        affinity=psutil.Process().cpu_affinity() if hasattr(psutil.Process(), 'cpu_affinity') else None,
        memory_total=psutil.virtual_memory().total, processor=platform.processor())
    for path in ('/sys/fs/cgroup/cpu.max', '/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/cpuset.cpus.effective'):
        p = Path(path)
        if p.is_file():
            info[path.rsplit('/', 1)[-1]] = p.read_text().strip()
    return info


def worker_limit(requested, per_worker_gib=2.0):
    machine = machine_info()
    limits = [requested, int(os.environ.get('W2_EFFECTIVE_CPUS', '1'))]
    if machine['affinity']:
        limits.append(len(machine['affinity']))
    cpu_max = machine.get('cpu.max', 'max 100000').split()
    if cpu_max[0] != 'max':
        limits.append(max(1, int(int(cpu_max[0])/int(cpu_max[1]))))
    memory = psutil.virtual_memory().available
    if machine.get('memory.max', 'max') != 'max':
        memory = min(memory, int(machine['memory.max']))
    limits.append(max(1, int(memory/(per_worker_gib*2**30))))
    return max(1, min(limits))


def execute_jobs(jobs, workers, run):
    results = []
    started = time.perf_counter()
    # Прогрев JIT-кеша в родителе до пула: уменьшает одновременную компиляцию
    # Numba в воркерах (наблюдались редкие segfault при высоком параллелизме).
    warmup()
    pool = mp.get_context('spawn').Pool(workers, initializer=worker_init)
    pending = [(job, pool.apply_async(run_case, (job,))) for job in jobs]
    try:
        while pending:
            run.check_budget()
            ready = [(j, f) for j, f in pending if f.ready()]
            for job, future in ready:
                try:
                    result = future.get()
                except Exception as exc:
                    result = dict(engine=job['engine'], orbit_id=job['case']['orbit_id'],
                        run_status='failed', error=f'{type(exc).__name__}: {exc}',
                        physical_outcome='unresolved', tight=bool(job.get('tight')))
                results.append(result)
                pending.remove((job, future))
                write_json(run.folder/'results/rows.json', results)
            if not ready:
                time.sleep(.05)
    except (TimeoutError, KeyboardInterrupt):
        for job, _ in pending:
            results.append(dict(engine=job['engine'], orbit_id=job['case']['orbit_id'],
                run_status='not_completed_before_stage_deadline', physical_outcome='unresolved',
                tight=bool(job.get('tight')), error='Дедлайн стадии/прерывание'))
    finally:
        pool.terminate()
        pool.join()
    elapsed = time.perf_counter()-started
    write_json(run.folder/'results/rows.json', results)
    return dict(rows=results, workers=workers, batch_wall_seconds=elapsed,
                batch_cpu_seconds=sum(r.get('cpu_seconds', 0.) for r in results))


def stage(root, name, seconds, workers):
    cfg, cases = load_cases(root)
    run = RunContext(root, 'W2-'+name, wall_seconds=min(600, seconds), output_mib=256)
    with run:
        run.save_config(cfg | dict(stage=name, workers=workers, data_kind='development_numerical_comparison'))
        for path in [cfg['model_path'], cfg['states_path'], cfg['contrasts_path']]:
            run.ledger.read(path, registered=True)
        run.save_code(root/'scripts/run_w2_comparison.py')
        run.manifest['remote'] = dict(instance_id=os.environ.get('W2_INSTANCE_ID'), machine=machine_info())
        write_json(run.folder/'initial_conditions/cases.json', cases)
        output = {}
        if name == 'checks':
            commands = [[sys.executable, '-m', 'pytest', '-q'],
                        [sys.executable, '-m', 'ruff', 'check', 'src', 'scripts', 'tests'],
                        [sys.executable, 'scripts/project.py', 'validate'],
                        [sys.executable, 'scripts/project.py', 'smoke']]
            results = []
            for i, command in enumerate(commands):
                completed = run.subprocess(command, capture_output=True, text=True, encoding='utf-8')
                (run.folder/f'logs/check_{i}.txt').write_text(completed.stdout+completed.stderr, encoding='utf-8')
                results.append(dict(command=command, exit_code=completed.returncode))
            output = dict(checks=results)
            run.validate({str(i): r['exit_code'] == 0 for i, r in enumerate(results)}, scope='code_and_installation_checks')
        elif name == 'profile':
            output = dict(warmup_seconds=warmup(), profile=profile_original(cases[0]))
            run.validate({'profile_recorded': True}, scope='profiling_only')
        elif name == 'analytic':
            output = dict(controls=[])
            for engine in ENGINES:
                output['controls'].append(analytical_controls(engine, run.check_budget))
                write_json(run.folder/'results/summary.json', output)
            run.validate({r['engine']: bool(r['passed']) for r in output['controls']}, scope='analytic_controls_with_high_e_stress')
        elif name == 'interpolation':
            output = interpolation_checks(cases, run)
            run.validate({r['orbit_id']: r['passed'] for r in output['comparisons']}, scope='external_cache_refinement')
        else:
            selected = performance_cases(cases)
            jobs = []
            if name in ('normal', 'tight'):
                engines = (*ENGINES, 'dop853_original') if name == 'normal' else ENGINES
                for i, c in enumerate(cases):
                    order = engines[i % len(engines):]+engines[:i % len(engines)]
                    jobs.extend(dict(case=c, engine=e, tight=name == 'tight') for e in order)
            elif name.startswith('performance'):
                repeat = int(name[-1])
                for i, c in enumerate(selected):
                    shift = (i+repeat) % 3
                    for e in ENGINES[shift:]+ENGINES[:shift]:
                        jobs.append(dict(case=c, engine=e, repeat=repeat))
            elif name == 'reuse':
                jobs = [dict(case=c, engine='hierarchical_cached', reuse_cache=True) for c in selected]
            elif name.startswith('scaling'):
                # Один и тот же набор из 36 заданий, один движок, каждый размер пула.
                jobs = [dict(case=c, engine='dop853_jit', repeat=k) for k in range(3) for c in selected]
            elif name == 'extended':
                jobs = [dict(case=c, engine=e, periods=20.) for c in selected for e in ENGINES]
            else:
                raise ValueError('Неизвестная стадия '+name)
            output = execute_jobs(jobs, workers, run)
            run.manifest['resources']['workers'] = workers
            run.validate({'all_jobs_completed': all(r['run_status'] == 'completed' for r in output['rows'])},
                         scope='execution_only_accuracy_evaluated_in_campaign')
        write_json(run.folder/'results/summary.json', output)
    print(json.dumps(dict(run_id=run.run_id, status=run.manifest['status']), ensure_ascii=False), flush=True)
    return run.run_id


def interpolation_checks(cases, run):
    from submoon_research.dynamics.engine_compare import integrate_engine
    results = []
    for c in performance_cases(cases):
        p = c['period_seconds']
        horizon = 5*p
        coarse = MassiveCache(c['state'], c['gms'], c['figures'], epsilon=1e-12)
        coarse.extend(horizon, run.check_budget)
        finer = MassiveCache(c['state'], c['gms'], c['figures'], epsilon=1e-12,
                             max_step=max(s.right-s.left for s in coarse.segments)/2)
        grid = [t for s in coarse.segments for t in ((s.left+s.right)/2, s.right)]
        finer.extend(horizon, run.check_budget, split_grid=grid)
        kwargs = dict(period=p, mode=NumericMode(), hill_km=c['hill_km'],
                      escape_window=c['escape_window'], budget=run.check_budget)
        pair = [integrate_engine('hierarchical_cached', c['state'], c['gms'], c['figures'],
                    c['radii'], horizon, cache=cache, **kwargs) for cache in (coarse, finer)]
        enriched = [r | dict(a_km=c['a_km'], gms=c['gms'], period_seconds=p) for r in pair]
        check = compare_rows(*enriched, dict(position_a=1e-7, velocity_na=1e-7,
            event_time_period=1e-6, massive_energy=1e-8))
        results.append(dict(orbit_id=c['orbit_id'], passed=check['status'] == 'passed',
            comparison=check, coarse_steps=len(coarse.segments), fine_steps=len(finer.segments),
            coarse_cache_identity=coarse.identity, fine_cache_identity=finer.identity))
        write_json(run.folder/'results/interpolation.json', results)
    return dict(comparisons=results)


def summarize(root, stage_ids, thresholds):
    loaded = {}
    for name, run_id in stage_ids.items():
        p = root/'runs'/run_id/'results/summary.json'
        if p.is_file():
            loaded[name] = json.loads(p.read_text(encoding='utf-8'))
    normal = {(r['orbit_id'], r['engine']): r for r in loaded.get('normal', {}).get('rows', [])}
    tight = {(r['orbit_id'], r['engine']): r for r in loaded.get('tight', {}).get('rows', [])}
    comparisons = []
    for key in sorted(normal):
        orbit, engine = key
        if engine in ENGINES and key in tight:
            comparisons.append(dict(orbit_id=orbit, engine=engine, kind='self_refinement',
                **compare_rows(normal[key], tight[key], thresholds)))
        reference = tight.get((orbit, 'ias15_reboundx'))
        if engine != 'ias15_reboundx' and reference:
            comparisons.append(dict(orbit_id=orbit, engine=engine, kind='versus_tight_ias15',
                **compare_rows(normal[key], reference, thresholds)))
    performance = [r for name, data in loaded.items() if name.startswith('performance') for r in data.get('rows', [])]
    common_ids = set.intersection(*[
        {r['orbit_id'] for r in performance if r['engine'] == engine and r['run_status'] == 'completed'}
        for engine in ENGINES]) if performance else set()
    # Только все три повтора всех трёх движков, без выжившего удачного повтора.
    common_ids = {o for o in common_ids if all(sum(r['orbit_id'] == o and r['engine'] == e
        and r['run_status'] == 'completed' for r in performance) == 3 for e in ENGINES)}
    timing = {}
    for e in ENGINES:
        rows = [r for r in performance if r['engine'] == e and r['orbit_id'] in common_ids]
        seconds = [r['elapsed_including_setup_seconds'] for r in rows]
        errors = [c for c in comparisons if c['engine'] == e and c['status'] != 'passed']
        timing[e] = dict(common_cases=len(common_ids), calls=len(rows),
            total_seconds=sum(seconds) if seconds else None,
            median_seconds=float(np.median(seconds)) if seconds else None,
            max_seconds=max(seconds) if seconds else None,
            short_accuracy_failures=len(errors),
            scientific_admission='not_validated_for_10000_years')
    scaling = {}
    for name, data in loaded.items():
        if name.startswith('scaling'):
            rows = data.get('rows', [])
            scaling[data.get('workers', int(name[7:]))] = dict(
                completed=sum(r['run_status'] == 'completed' for r in rows), planned=36,
                wall_seconds=data.get('batch_wall_seconds'), cpu_seconds=data.get('batch_cpu_seconds'),
                pid_count=len({r.get('worker_pid') for r in rows if r.get('worker_pid')}))
    baseline = scaling.get(1)
    for data in scaling.values():
        data['speedup'] = (baseline['wall_seconds']/data['wall_seconds']
            if baseline and baseline['completed'] == data['completed'] == 36 else None)
    return dict(comparisons=comparisons, timing=timing,
        scaling=scaling,
        stages=stage_ids, production_allowed=False, full_pilot_allowed=False,
        selection='Review coverage, analytic stress, interpolation and restart before choosing.',
        limitations=['Короткие разработочные случаи, не доля выживания и не долгий допуск',
                     'CPU масштабирование оценивается отдельно от однопроцессной скорости'])


def campaign(root, seconds, max_workers):
    if os.environ.get('W2_REMOTE_EXECUTION') != '1':
        raise RuntimeError('По поручению пользователя вычислительная серия запускается на сервере')
    cfg, _ = load_cases(root)
    budget_path = root/'scratch/w2_campaign_budget.json'
    consumed = json.loads(budget_path.read_text()).get('consumed_seconds', 0.) if budget_path.exists() else 0.
    seconds = min(seconds, max(0., 3600-consumed))
    if seconds < 10:
        raise RuntimeError('Суммарный час тестовой серии исчерпан')
    started = time.perf_counter()
    folder = root/'runs'/('W2-campaign-'+time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())+'-'+uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    manifest = dict(run_id=folder.name, status='running', started_utc=utc_now(),
        data_kind='development_comparison', environment=environment(), machine=machine_info(),
        instance_id=os.environ.get('W2_INSTANCE_ID'), campaign_wall_seconds=seconds,
        stages={}, commands=[], production_allowed=False, artifacts_sha256={})
    write_json(folder/'manifest.json', manifest)
    write_json(folder/'config.json', cfg)
    # Проверки одиночного ядра не конкурируют с масштабированием ансамбля.
    sequence = [('checks', 420, 1), ('profile', 120, 1), ('analytic', 600, 1),
                ('normal', 480, min(4, max_workers)), ('tight', 480, min(4, max_workers)),
                ('performance0', 220, 1), ('performance1', 220, 1), ('performance2', 220, 1),
                ('reuse', 100, 1), ('interpolation', 240, 1), ('extended', 180, min(4, max_workers))]
    sequence += [('scaling'+str(n), 60, n) for n in (1, 2, 4, 8, 16) if n <= max_workers]
    try:
        for name, duration, workers in sequence:
            remaining = seconds-(time.perf_counter()-started)-30
            if remaining < 10:
                break
            duration = min(duration, remaining, 600)
            command = [sys.executable, '-u', 'scripts/run_w2_comparison.py', 'stage',
                       '--stage', name, '--seconds', str(max(1, duration-5)), '--workers', str(workers)]
            log = folder/(name+'.log')
            before = {p.name for p in (root/'runs').glob('W2-'+name+'-*')}
            with log.open('w', encoding='utf-8') as stream:
                process = subprocess.Popen(command, cwd=root, stdout=stream, stderr=subprocess.STDOUT,
                                           start_new_session=os.name != 'nt')
                try:
                    code = process.wait(timeout=duration)
                except subprocess.TimeoutExpired:
                    for child in psutil.Process(process.pid).children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    process.kill()
                    process.wait()
                    code = 124
            created = sorted(p.name for p in (root/'runs').glob('W2-'+name+'-*') if p.name not in before)
            if created:
                manifest['stages'][name] = created[-1]
            manifest['commands'].append(dict(stage=name, command=command, exit_code=code))
            write_json(folder/'manifest.json', manifest)
            print(json.dumps(dict(stage=name, exit_code=code, elapsed=time.perf_counter()-started)), flush=True)
            if name == 'checks':
                check_file = root/'runs'/manifest['stages'].get('checks', '')/'results/summary.json'
                if code != 0 or not check_file.is_file():
                    raise RuntimeError('Инфраструктурные проверки не завершены; научная серия не начата')
                checks = json.loads(check_file.read_text())['checks']
                if any(c['exit_code'] != 0 for c in checks):
                    raise RuntimeError('Проверки кода failed; исправить до сравнительной серии')
        summary = summarize(root, manifest['stages'], cfg['thresholds'])
        write_json(folder/'summary.json', summary)
        plot_comparison(folder, summary)
        manifest['status'] = 'completed'
    except Exception as exc:
        manifest['status'], manifest['error'] = 'failed', str(exc)
    finally:
        manifest.update(finished_utc=utc_now(), wall_seconds=time.perf_counter()-started)
        manifest['artifacts_sha256'] = {p.name: sha256(p) for p in folder.iterdir() if p.is_file() and p.name != 'manifest.json'}
        write_json(folder/'manifest.json', manifest)
        write_json(budget_path, dict(consumed_seconds=consumed+manifest['wall_seconds'],
                                    last_campaign=folder.name))
    print(json.dumps(dict(campaign_id=folder.name, status=manifest['status'])), flush=True)
    return 0 if manifest['status'] == 'completed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('campaign', 'stage', 'summarize'))
    parser.add_argument('--stage')
    parser.add_argument('--seconds', type=float, default=3600)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--campaign')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if args.action == 'campaign':
        return campaign(root, min(args.seconds, 3600), worker_limit(args.workers))
    if args.action == 'stage':
        stage(root, args.stage, args.seconds, worker_limit(args.workers))
        return 0
    cfg, _ = load_cases(root)
    folder = root/'runs'/args.campaign
    manifest = json.loads((folder/'manifest.json').read_text())
    target = root/'reports/scientific'/('W2-reanalysis-'+uuid.uuid4().hex[:8]+'.json')
    write_json(target, summarize(root, manifest['stages'], cfg['thresholds']))
    return 0


def plot_comparison(folder, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4), constrained_layout=True)
    for engine, timing in summary['timing'].items():
        errors = [max(c.get('position_a', 0), c.get('velocity_na', 0))
                  for c in summary['comparisons'] if c['engine'] == engine and 'position_a' in c]
        if timing['median_seconds'] is not None and errors:
            ax.scatter(timing['median_seconds'], max(max(errors), 1e-16), label=engine)
    ax.axhline(1e-6, color='gray', linestyle='--', label='short-test tolerance')
    ax.set(xscale='log', yscale='log', xlabel='Median wall seconds / orbit (common completed set)',
           ylabel='Maximum short-test normalized state error', title='W2 development comparison; no long-horizon admission')
    ax.legend(fontsize=8)
    fig.savefig(folder/'accuracy_vs_time.png', dpi=160)
    plt.close(fig)
