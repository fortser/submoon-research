"""Разработочные входы, ограниченные задания и честное сравнение A/B/C."""
from dataclasses import asdict
import cProfile
import io
import json
import math
import os
from pathlib import Path
import pstats
import time

import numpy as np
import psutil
import yaml

from submoon_research.catalog.nominal_model import validate_model
from submoon_research.contracts.nominal_states import validate_nominal_states
from submoon_research.dynamics.engine_compare import integrate_engine, NumericMode
from submoon_research.dynamics.initialization import physical_inputs, PARENTS
from submoon_research.dynamics.native_ias15 import MassiveCache
from submoon_research.dynamics.kepler_reference import pericenter_seed, pericenter_reference
from submoon_research.sampling.design import DomainSpec, elements_to_cartesian
from submoon_research.workflows.smoke import sha256


def load_cases(root):
    root = Path(root)
    cfg = yaml.safe_load((root/'configs/experiments/W2_three_engines_v1.yaml').read_text(encoding='utf-8'))
    for path, expected in cfg['inputs_sha256'].items():
        if sha256(root/path) != expected:
            raise ValueError('Изменился закреплённый вход '+path)
    model = validate_model(json.loads((root/cfg['model_path']).read_text(encoding='utf-8')))
    states = validate_nominal_states(json.loads((root/cfg['states_path']).read_text(encoding='utf-8')))
    contrasts = json.loads((root/cfg['contrasts_path']).read_text(encoding='utf-8'))['records']
    if len(contrasts) != 36 or set(c['host'] for c in contrasts) != set(cfg['hosts']):
        raise ValueError('Неверный состав разработочных входов')
    setups = {}
    for host in cfg['hosts']:
        names, massive, gms, figs, radii, a_host, basis = physical_inputs(host, model, states)
        gm_parent = model['bodies'][PARENTS[host]]['gm']['value']
        domain = DomainSpec(domain_id=host+'-nominal-v1', gm_host=gms[0], gm_parent=gm_parent,
            a_host_km=a_host, reference_radius_km=radii[0]-model['submoon_radius_km'],
            submoon_radius_km=model['submoon_radius_km'], eccentricity_max=0.3, contact_buffer=0.02)
        setups[host] = dict(gms=gms.tolist(), figures=figs, radii=radii,
            hill_km=domain.hill_km, escape_window=2*math.pi*math.sqrt(a_host**3/(gm_parent+gms[0])),
            massive=massive.tolist(), basis=basis.tolist(), names=names)
    cases = []
    for row in contrasts:
        case = dict(row) | setups[row['host']]
        case['sample_kind'] = 'development_contrast_not_probability_sample'
        cases.append(case)
    for original in list(cases):
        if original['inclination_degrees'] == 90:
            case = dict(original)
            case['orbit_id'] += '-phase'
            ell, omega, node = cfg['phase_angles_rad']
            probe = elements_to_cartesian(case['gms'][0], case['a_km'], case['eccentricity'],
                0., ell, omega, node, np.array(case['basis']))
            case['state'] = np.vstack([case['massive'], probe]).ravel().tolist()
            case['phase_angles_rad'] = [ell, omega, node]
            cases.append(case)
    return cfg, cases


def performance_cases(cases):
    return [c for c in cases if c['eccentricity'] == 0.3
            and c['inclination_degrees'] in (0, 180) and not c['orbit_id'].endswith('-phase')]


def warmup():
    start = time.perf_counter()
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1, 0]])
    # Компиляция типовых форм, включая нетривиальный массив фигур, без серии орбит.
    from submoon_research.dynamics import fast_forces
    fs = fast_forces.figure_array([], 1)
    fast_forces.rhs(initial.ravel(), np.array([1.]), fs)
    fast_forces.energy(initial.ravel(), np.array([1.]), fs)
    return time.perf_counter()-start


_WORKER_CACHES = {}


def run_case(job):
    """Pickle-safe worker: собственные состояния/кеш, никаких общих файлов записи."""
    case, engine = job['case'], job['engine']
    mode = NumericMode.tight() if job.get('tight') else NumericMode()
    if 'numeric' in job:
        mode = NumericMode(**job['numeric'])
    started = time.perf_counter()
    timeout = job.get('wall_seconds', 10.)
    peak = 0
    process = psutil.Process()
    next_resource_check = 0.

    def budget():
        nonlocal peak, next_resource_check
        now = time.perf_counter()
        if now-started >= timeout:
            raise TimeoutError('Лимит вычислений; не физический исход')
        if now >= next_resource_check:
            peak = max(peak, process.memory_info().rss)
            next_resource_check = now+0.1
            if peak > job.get('memory_bytes', 2*2**30):
                raise MemoryError('Лимит RAM worker')

    periods = job.get('periods', 5.)
    horizon = case['period_seconds']*periods
    cache = None
    if engine == 'hierarchical_cached' and job.get('reuse_cache'):
        key = (case['host'], mode.cache_epsilon)
        if key not in _WORKER_CACHES:
            _WORKER_CACHES[key] = MassiveCache(case['state'], case['gms'], case['figures'],
                                              epsilon=mode.cache_epsilon)
        cache = _WORKER_CACHES[key]
    output = integrate_engine(engine, case['state'], case['gms'], case['figures'], case['radii'],
        horizon, period=case['period_seconds'], mode=mode,
        hill_km=case.get('hill_km'), escape_window=case.get('escape_window'),
        times=np.linspace(0, horizon, job.get('samples', 65)), budget=budget, cache=cache,
        resume=job.get('resume'))
    output.update(orbit_id=case['orbit_id'], host=case['host'], radial=case.get('radial'),
        period_seconds=case['period_seconds'], a_km=case['a_km'], gms=case['gms'],
        horizon_seconds=horizon, numeric=asdict(mode), tight=bool(job.get('tight')),
        repeat=job.get('repeat'), memory_peak_bytes=peak, worker_pid=os.getpid(),
        elapsed_including_setup_seconds=time.perf_counter()-started,
        reused_cache=bool(job.get('reuse_cache')), sample_kind=case.get('sample_kind'))
    return output


def compare_rows(a, b, thresholds):
    if a['run_status'] != 'completed' or b['run_status'] != 'completed':
        return dict(status='inconclusive', reason='Есть незавершённая интеграция')
    if a['physical_outcome'] != b['physical_outcome']:
        return dict(status='failed', reason='Разные исходы')
    t0 = {x['time']: x for x in a['samples']}
    t1 = {x['time']: x for x in b['samples']}
    common = sorted(t0.keys() & t1.keys())
    # Конец/контакт также проверяется, даже если до события нет внутренних diagnostics.
    dr = [np.linalg.norm(np.array(t0[t]['state'][-6:-3])-t1[t]['state'][-6:-3]) for t in common]
    dv = [np.linalg.norm(np.array(t0[t]['state'][-3:])-t1[t]['state'][-3:]) for t in common]
    dr.append(np.linalg.norm(np.array(a['final_state'][-6:-3])-b['final_state'][-6:-3]))
    dv.append(np.linalg.norm(np.array(a['final_state'][-3:])-b['final_state'][-3:]))
    rerr = float(max(dr)/a['a_km'])
    verr = float(max(dv)/math.sqrt(a['gms'][0]/a['a_km']))
    event_error = abs(a['last_valid_time']-b['last_valid_time'])/a['period_seconds']
    same_body = (a['event'] or {}).get('body_index') == (b['event'] or {}).get('body_index')
    event_ok = same_body and event_error <= thresholds['event_time_period']
    passed = (rerr <= thresholds['position_a'] and verr <= thresholds['velocity_na'] and event_ok
              and max(a['massive_energy_relative_drift'], b['massive_energy_relative_drift'])
              <= thresholds['massive_energy'])
    return dict(status='passed' if passed else 'failed', position_a=rerr, velocity_na=verr,
        event_time_period=event_error, same_body=same_body, common_samples=len(common),
        phase_space_comparison='short_trajectory_only_not_long_chaotic_admission')


def analytical_controls(engine, budget):
    mode = NumericMode(1e-13, 1e-15, 1e-15, 0.02, 1e-12, 1e-13)
    records = []
    for e in (0., 0.5, 0.9, 0.99):
        budget()
        initial = np.vstack([np.zeros(6), pericenter_seed(e)])
        times = np.linspace(0, 20*math.pi, 513)
        reference, meta = pericenter_reference(initial[-1], times)
        result = integrate_engine(engine, initial, [1.], [], [1e-4], times[-1],
            period=2*math.pi, mode=mode, times=times, budget=budget)
        if result['run_status'] != 'completed':
            records.append(dict(test='kepler', eccentricity=e, result=result, passed=False))
            continue
        actual = np.array([s['state'][-6:] for s in result['samples']])
        pos = np.linalg.norm(actual[:, :3]-reference[:, :3], axis=1).max()
        vel = np.linalg.norm(actual[:, 3:]-reference[:, 3:], axis=1).max()
        energy = np.sum(actual[:, 3:]**2, axis=1)/2-1/np.linalg.norm(actual[:, :3], axis=1)
        angular = np.linalg.norm(np.cross(actual[:, :3], actual[:, 3:]), axis=1)
        de = np.max(abs(energy/meta['energy_from_rounded_seed']-1))
        dl = np.max(abs(angular/meta['angular_momentum_from_rounded_seed']-1))
        records.append(dict(test='kepler', eccentricity=e, position=float(pos), velocity=float(vel),
            energy=float(de), angular_momentum=float(dl), reference=meta,
            passed=bool(pos <= 1e-8 and vel <= 1e-8 and de <= 1e-9 and dl <= 1e-9),
            wall_seconds=result['wall_seconds'], cpu_seconds=result['cpu_seconds']))
    mu = 0.01
    x, y = .5+.005, math.sqrt(3)/2
    initial = np.array([[0., 0, 0, 0, 0, 0], [1., 0, 0, 0, 1., 0], [x, y, 0, -y, x-.001, 0]])
    times = np.linspace(0, 20*math.pi, 1025)
    result = integrate_engine(engine, initial, [1-mu, mu], [], [1e-4, 1e-4], times[-1],
                              period=2*math.pi, mode=mode, times=times, budget=budget)
    if result['run_status'] == 'completed':
        s = np.array([s['state'][-6:] for s in result['samples']])
        c, sn = np.cos(times), np.sin(times)
        rx, ry = c*s[:, 0]+sn*s[:, 1], -sn*s[:, 0]+c*s[:, 1]
        vx, vy = c*s[:, 3]+sn*s[:, 4]+ry, -sn*s[:, 3]+c*s[:, 4]-rx
        jacobi = (rx-mu)**2+ry**2+2*(1-mu)/np.hypot(rx, ry)+2*mu/np.hypot(rx-1, ry)-vx**2-vy**2
        drift = float(np.max(abs(jacobi/jacobi[0]-1)))
        records.append(dict(test='CR3BP', jacobi_relative_drift=drift, passed=drift <= 1e-9))
    else:
        records.append(dict(test='CR3BP', passed=False, error=result['error']))
    for j2 in (1e-4, 5e-5):
        initial = np.vstack([np.zeros(6), elements_to_cartesian(1., 1., .1, math.cos(.6), 0., 0., 0., np.eye(3))])
        figs = [dict(index=0, j2=j2, radius=.5, pole=[0., 0., 1.])]
        times = np.linspace(0, 160*math.pi, 4097)
        result = integrate_engine(engine, initial, [1.], figs, [0.01], times[-1],
                                  period=2*math.pi, mode=mode, times=times, budget=budget)
        if result['run_status'] != 'completed':
            records.append(dict(test='J2', j2=j2, passed=False, error=result['error']))
            continue
        s = np.array([s['state'][-6:] for s in result['samples']])
        r, v = s[:, :3], s[:, 3:]
        rad = np.linalg.norm(r, axis=1)
        angular = np.cross(r, v)
        node = np.cross(np.array([0., 0., 1.]), angular)
        ecc = np.cross(v, angular)-r/rad[:, None]
        longitude = np.unwrap(np.arctan2(node[:, 1], node[:, 0]))
        peri = np.unwrap(np.arctan2(np.sum(np.cross(node, ecc)*angular, axis=1)
            /np.linalg.norm(angular, axis=1), np.sum(node*ecc, axis=1)))
        scale = j2*.25/(1-.01)**2
        errors = [abs(np.polyfit(times, longitude, 1)[0]/(-1.5*scale*math.cos(.6))-1),
                  abs(np.polyfit(times, peri, 1)[0]/(.75*scale*(5*math.cos(.6)**2-1))-1)]
        energy = np.sum(v*v, axis=1)/2-1/rad+j2*.25*(3*(r[:, 2]/rad)**2-1)/(2*rad**3)
        drift = float(np.max(abs(energy/energy[0]-1)))
        records.append(dict(test='J2', j2=j2, node_relative_error=float(errors[0]),
            peri_relative_error=float(errors[1]), energy_relative_drift=drift,
            passed=bool(max(errors) <= .01 and drift <= 1e-9)))
    return dict(engine=engine, tests=records, passed=all(x['passed'] for x in records),
                scope='synthetic_analytic_controls_not_production')


def profile_original(case, seconds=3):
    profile = cProfile.Profile()
    profile.enable()
    result = run_case(dict(case=case, engine='dop853_original', wall_seconds=seconds))
    profile.disable()
    output = io.StringIO()
    pstats.Stats(profile, stream=output).sort_stats('cumulative').print_stats(35)
    return dict(result=result, profile=output.getvalue())
