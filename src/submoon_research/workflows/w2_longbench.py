"""Машинный бенчмарк длинного горизонта W2-T006 (CPU-матрица).

Замеряет Q1 (1 ядро), Q2 (масштабирование), Q3 (полная аллокация) и Q5
(добросовестность cgroup/NUMA) для замороженного набора траекторий и движка.
Это характеристика железа, а не научный результат: доли выживания, события и
допуск длинного горизонта здесь не оцениваются.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import yaml

from submoon_research.execution import RunContext
from submoon_research.workflows.w2_campaign import execute_jobs, machine_info, worker_limit
from submoon_research.workflows.w2_comparison import load_cases
from submoon_research.workflows.smoke import environment, sha256, write_json

CONFIG_PATH = 'configs/experiments/W2_longbench_v1.yaml'
ENGINE_SHORT = {'dop853_jit': 'A', 'ias15_reboundx': 'B', 'hierarchical_cached': 'C'}
ACCEPTED_STATUS = ('completed', 'partial', 'failed', 'not_completed_before_stage_deadline')


def load_longbench(root):
    root = Path(root)
    cfg = yaml.safe_load((root/CONFIG_PATH).read_text(encoding='utf-8'))
    for path, expected in cfg['inputs_sha256'].items():
        if sha256(root/path) != expected:
            raise ValueError('Изменился закреплённый вход '+path)
    _, all_cases = load_cases(root)
    cases = [c for c in all_cases if c['radial'] == 'inner'
             and not c['orbit_id'].endswith('-phase')
             and c['eccentricity'] in cfg['eccentricities']
             and c['inclination_degrees'] in cfg['inclinations_degrees']
             and c['host'] in cfg['hosts']]
    expected_count = len(cfg['hosts'])*len(cfg['eccentricities'])*len(cfg['inclinations_degrees'])
    if len(cases) != expected_count:
        raise ValueError('Неполный состав внутренних случаев для бенчмарка')
    if cfg['reference_case_id'] not in {c['orbit_id'] for c in cases}:
        raise ValueError('Эталонный случай отсутствует в наборе')
    frozen = cfg.get('frozen_survivors')
    if frozen:
        wanted = set(frozen)
        known = {c['orbit_id'] for c in cases}
        if not wanted <= known:
            raise ValueError('frozen_survivors содержит неизвестные случаи')
        if cfg['reference_case_id'] not in wanted:
            raise ValueError('Эталонный случай исключён из frozen_survivors')
        cases = [c for c in cases if c['orbit_id'] in wanted]
    return cfg, cases


def machine_profile():
    info = dict(machine_info())
    info['processor'] = platform.processor()
    cpuinfo = Path('/proc/cpuinfo')
    if cpuinfo.is_file():
        text = cpuinfo.read_text(encoding='utf-8', errors='replace')
        model = re.search(r'^model name\s*:\s*(.+)$', text, re.M)
        if model:
            info['cpu_model'] = model.group(1).strip()
        cores = set()
        for block in re.split(r'\n\s*\n', text.strip()):
            fields = {k.strip(): v.strip() for k, v in
                      (line.split(':', 1) for line in block.splitlines() if ':' in line)}
            if fields:
                cores.add((fields.get('physical id', '0'),
                           fields.get('core id', fields.get('processor', '?'))))
        info['physical_cores'] = len(cores)
    try:
        lscpu = subprocess.run(['lscpu'], capture_output=True, text=True, timeout=10,
                               check=False).stdout
        info['lscpu'] = {line.split(':', 1)[0].strip(): line.split(':', 1)[1].strip()
                         for line in lscpu.splitlines() if ':' in line
                         and line.split(':', 1)[0].strip() in (
                             'Architecture', 'CPU(s)', 'Thread(s) per core', 'Core(s) per socket',
                             'Socket(s)', 'NUMA node(s)', 'Model name', 'CPU max MHz',
                             'CPU min MHz')}
    except (OSError, subprocess.SubprocessError):
        info['lscpu'] = None
    return info


def _jobs(cases, engine, years, wall_seconds, *, count=None, repeats=1):
    ordered = []
    while len(ordered) < (count or len(cases)):
        ordered += list(cases)
    ordered = ordered[:count or len(cases)]
    return [dict(case=case, engine=engine, horizon_years=years, wall_seconds=wall_seconds,
                 memory_bytes=int(1.0*2**30), repeat=repeat, order=index)
            for index, case in enumerate(ordered) for repeat in range(repeats)]


RESERVE_GIB_PER_WORKER = 1.0


def _stage(root, cfg, name, *, engine, years, workers, jobs, description):
    effective = worker_limit(workers, per_worker_gib=RESERVE_GIB_PER_WORKER)
    run = RunContext(root, 'W2-longbench-'+name, wall_seconds=cfg['stage_wall_seconds'],
                     output_mib=cfg['cache_mib'])
    with run:
        run.save_config(cfg | dict(stage=name, description=description, engine=engine,
                                   horizon_years=years, requested_workers=workers,
                                   effective_workers=effective,
                                   data_kind='development_machine_benchmark',
                                   production_allowed=False))
        for path in cfg['inputs_sha256']:
            run.ledger.read(path, registered=True)
        run.save_code(root/'scripts/bench_cpu_matrix.py')
        profile = machine_profile()
        run.manifest['remote'] = dict(instance_id=os.environ.get('W2_INSTANCE_ID'),
                                      machine=profile, effective_workers=effective)
        run.manifest['resources']['workers'] = effective
        write_json(run.folder/'initial_conditions/jobs.json',
                   [dict(orbit_id=j['case']['orbit_id'], engine=j['engine'],
                         horizon_years=j['horizon_years'], wall_seconds=j['wall_seconds'])
                    for j in jobs])
        output = execute_jobs(jobs, effective, run)
        output.update(stage=name, engine=engine, horizon_years=years,
                      effective_workers=effective, machine=profile)
        write_json(run.folder/'results/summary.json', output)
        run.validate({'all_jobs_reported': all(r.get('run_status') in ACCEPTED_STATUS
                                               for r in output['rows'])},
                     scope='machine_benchmark_execution_only_no_scientific_admission')
    return run.run_id, output


def survival(root, years, workers, case_ids=None):
    cfg, cases = load_longbench(root)
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c['orbit_id'] in wanted]
        if len(cases) != len(wanted):
            raise ValueError('Запрошены неизвестные случаи: '+repr(sorted(wanted - {c["orbit_id"] for c in cases})))
    if not cases:
        raise ValueError('Пустой набор случаев для проверки выживания')
    jobs = _jobs(cases, 'dop853_jit', years, cfg['case_wall_seconds'])
    run_id, output = _stage(root, cfg, 'survival', engine='dop853_jit', years=years,
                            workers=workers, jobs=jobs,
                            description='проверка выживания внутренних случаев до горизонта')
    survived = sorted({r['orbit_id'] for r in output['rows'] if r['run_status'] == 'completed'})
    events = sorted({r['orbit_id'] for r in output['rows']
                     if r['run_status'] == 'completed' and r.get('physical_outcome') != 'survived'})
    incomplete = sorted({r['orbit_id'] for r in output['rows'] if r['run_status'] != 'completed'})
    write_json(root/'runs'/run_id/'results/survival.json',
               dict(horizon_years=years, survived=survived, events=events, incomplete=incomplete))
    return run_id


def single(root, engine, years, repeats, workers):
    cfg, cases = load_longbench(root)
    reference = [c for c in cases if c['orbit_id'] == cfg['reference_case_id']]
    jobs = _jobs(reference, engine, years, cfg['case_wall_seconds'], repeats=repeats)
    return _stage(root, cfg, 'single-'+ENGINE_SHORT.get(engine, engine), engine=engine,
                  years=years, workers=workers, jobs=jobs,
                  description='одноядерная скорость эталонного случая, повторы')


def level(root, engine, years, workers, count):
    cfg, cases = load_longbench(root)
    # Чистое масштабирование: один и тот же эталонный случай W раз,
    # чтобы сравнение с single-A не смешивалось разной сложностью случаев.
    reference = [c for c in cases if c['orbit_id'] == cfg['reference_case_id']]
    jobs = _jobs(reference, engine, years, cfg['case_wall_seconds'], count=count)
    return _stage(root, cfg, f'level-{ENGINE_SHORT.get(engine, engine)}-{workers}',
                  engine=engine, years=years, workers=workers, jobs=jobs,
                  description='полная волна заданий на данном числе воркеров')


def planned_sequence(effective, physical):
    levels = [n for n in (1, 2, 4, 8, 16, 32, 64, 96, 128, 192) if n <= effective]
    if effective not in levels:
        levels.append(effective)
    if physical and physical <= effective and physical not in levels:
        levels.append(physical)
    levels = sorted(set(levels))
    sequence = [('survival', 600, effective), ('single-A', 600, 1)]
    sequence += [(f'level-A-{n}', 600, n) for n in levels if n != 1]
    sequence += [('single-B', 600, 1)]
    sequence += [(f'level-B-{min(8, effective)}', 600, min(8, effective))]
    return sequence


def campaign(root, seconds, max_workers):
    cfg, _ = load_longbench(root)
    if os.environ.get('W2_REMOTE_EXECUTION') != '1':
        raise RuntimeError('Машинная серия запускается на арендованном сервере')
    profile = machine_profile()
    effective = worker_limit(max_workers, per_worker_gib=RESERVE_GIB_PER_WORKER)
    physical = profile.get('physical_cores')
    sequence = planned_sequence(effective, physical)
    folder = root/'runs'/('W2-longbench-campaign-'
                         + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
                         + '-' + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    manifest = dict(run_id=folder.name, status='running',
                    started_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                    environment=environment(), machine=profile,
                    instance_id=os.environ.get('W2_INSTANCE_ID'), campaign_wall_seconds=seconds,
                    effective_workers=effective, physical_cores=physical, stages={}, commands=[])
    write_json(folder/'manifest.json', manifest)
    write_json(folder/'config.json', cfg)
    started = time.perf_counter()
    try:
        for name, duration, workers in sequence:
            remaining = seconds-(time.perf_counter()-started)-30
            if remaining < 10:
                break
            duration = min(duration, remaining)
            repeats = {'single-A': 2, 'single-B': 1}.get(name, 1)
            command = [sys.executable, '-u', 'scripts/bench_cpu_matrix.py', 'stage',
                       '--name', name, '--seconds', str(max(1, duration-5)),
                       '--workers', str(workers), '--repeats', str(repeats),
                       '--years', str(cfg['primary_years'])]
            log = folder/(name+'.log')
            before = {p.name for p in (root/'runs').glob('W2-longbench-'+name+'-*')}
            with log.open('w', encoding='utf-8') as stream:
                process = subprocess.Popen(command, cwd=root, stdout=stream,
                                           stderr=subprocess.STDOUT,
                                           start_new_session=os.name != 'nt')
                try:
                    code = process.wait(timeout=duration+30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code = 124
            created = sorted(p.name for p in (root/'runs').glob('W2-longbench-'+name+'-*')
                             if p.name not in before)
            manifest['stages'][name] = created[-1] if created else None
            manifest['commands'].append(dict(stage=name, exit_code=code))
            write_json(folder/'manifest.json', manifest)
            print(json.dumps(dict(stage=name, exit_code=code,
                                  elapsed=time.perf_counter()-started)), flush=True)
        write_json(folder/'matrix_summary.json', summarize(root, manifest))
        manifest['status'] = 'completed'
    except Exception as exc:
        manifest['status'], manifest['error'] = 'failed', str(exc)
    finally:
        manifest['finished_utc'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        manifest['wall_seconds'] = time.perf_counter()-started
        manifest['artifacts_sha256'] = {p.name: sha256(p) for p in folder.iterdir()
                                        if p.is_file() and p.name != 'manifest.json'}
        write_json(folder/'manifest.json', manifest)
    print(json.dumps(dict(campaign_id=folder.name, status=manifest['status'])), flush=True)
    return 0 if manifest['status'] == 'completed' else 1


def _load_stage(root, run_id):
    path = root/'runs'/run_id/'results/summary.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None


def _stats(seconds, years):
    rates = [years/s for s in seconds if s]
    return dict(median_seconds=float(np.median(seconds)) if seconds else None,
                min_seconds=min(seconds) if seconds else None,
                max_seconds=max(seconds) if seconds else None,
                years_per_second_median=float(np.median(rates)) if rates else None,
                years_per_second_min=min(rates) if rates else None,
                years_per_second_max=max(rates) if rates else None)


def summarize(root, manifest):
    stages = {}
    for name, run_id in manifest['stages'].items():
        data = _load_stage(root, run_id) if run_id else None
        if not data:
            continue
        rows = data.get('rows', [])
        completed = [r for r in rows if r['run_status'] == 'completed']
        years = data.get('horizon_years')
        if name.startswith('single'):
            stages[name] = dict(engine=data['engine'], jobs=len(rows), completed=len(completed),
                                **_stats([r['elapsed_including_setup_seconds'] for r in completed],
                                         years))
        else:
            total_years = years*len(completed)
            wall = data.get('batch_wall_seconds')
            stages[name] = dict(engine=data['engine'], workers=data.get('effective_workers'),
                                jobs=len(rows), completed=len(completed), wall_seconds=wall,
                                total_years=total_years,
                                years_per_machine_second=(total_years/wall if wall else None),
                                cpu_seconds=data.get('batch_cpu_seconds'))
    base = stages.get('single-A', {}).get('years_per_second_median')
    for name, data in stages.items():
        if name.startswith('level-A') and base and data.get('wall_seconds'):
            data['speedup'] = data['years_per_machine_second']/base
            data['efficiency'] = data['speedup']/max(1, data['workers'] or 1)
    return dict(stages=stages, effective_workers=manifest.get('effective_workers'),
                physical_cores=manifest.get('physical_cores'), machine=manifest.get('machine'),
                limitation='Машинная характеристика; не научный результат и не допуск W2/W1.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('survival', 'single', 'level', 'stage', 'campaign'))
    parser.add_argument('--engine', default='dop853_jit')
    parser.add_argument('--name')
    parser.add_argument('--years', type=float, default=5.0)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--count', type=int, default=None)
    parser.add_argument('--seconds', type=float, default=3600)
    parser.add_argument('--case-ids', default=None)
    args = parser.parse_args(argv)
    case_ids = args.case_ids.split(',') if args.case_ids else None
    root = Path(__file__).resolve().parents[3]
    if args.action == 'survival':
        survival(root, args.years, args.workers, case_ids)
    elif args.action == 'single':
        single(root, args.engine, args.years, args.repeats, args.workers)
    elif args.action == 'level':
        level(root, args.engine, args.years, args.workers, args.count or args.workers)
    elif args.action == 'campaign':
        return campaign(root, args.seconds, args.workers)
    elif args.action == 'stage':
        name = args.name
        if name == 'survival':
            survival(root, args.years, args.workers)
        elif name == 'single-A':
            single(root, 'dop853_jit', args.years, args.repeats, args.workers)
        elif name == 'single-B':
            single(root, 'ias15_reboundx', args.years, args.repeats, args.workers)
        elif name.startswith('level-A-'):
            level(root, 'dop853_jit', args.years, int(name.rsplit('-', 1)[-1]),
                  int(name.rsplit('-', 1)[-1]))
        elif name.startswith('level-B-'):
            level(root, 'ias15_reboundx', args.years, int(name.rsplit('-', 1)[-1]),
                  int(name.rsplit('-', 1)[-1]))
        else:
            raise ValueError('Неизвестная стадия '+name)
    return 0
