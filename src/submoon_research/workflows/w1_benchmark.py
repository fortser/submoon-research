"""Ограниченный контрастный benchmark: незавершённые случаи сохраняют стоимость."""
import math
import time

import numpy as np

from submoon_research.catalog.nominal_model import validate_model
from submoon_research.contracts.nominal_states import validate_nominal_states
from submoon_research.contracts.w1 import W1Design
from submoon_research.dynamics.baseline import integrate
from submoon_research.dynamics.initialization import physical_inputs, PARENTS
from submoon_research.events.escape import EscapeTracker
from submoon_research.sampling.design import DomainSpec, elements_to_cartesian
from submoon_research.workflows.smoke import write_json


def benchmark(run, *, seconds_per_case=4.0):
    config = run.ledger.yaml('configs/experiments/W0_nominal_benchmark_v1.yaml', registered=True)
    for name, digest in config['inputs_sha256'].items():
        run.ledger.bind(name, digest)
    model = validate_model(run.ledger.json(config['model_path']))
    states = validate_nominal_states(run.ledger.json(config['states_path']))
    design = W1Design.model_validate(run.ledger.yaml('configs/sampling/W1_design_v1.yaml', registered=True))
    run.save_config(dict(experiment_id='W1-cost-benchmark-v1', data_kind='real_nominal_cost_benchmark',
        design=design.model_dump(), case_wall_seconds=seconds_per_case,
        comparison_fraction=False, production_allowed=False,
        numeric_admission='DOP853_candidate_not_long_horizon_validated',
        options={k: config[k] for k in ('rtol', 'atol_position_km', 'atol_velocity_km_s', 'max_step_period_fraction')}))
    cases, costs, setups = [], [], {}
    for host in design.development:
        names, relative, gms, figures, radii, a_host, basis = physical_inputs(host, model, states)
        domain = DomainSpec(domain_id=host+'-nominal-v1', gm_host=float(gms[0]),
            gm_parent=float(model['bodies'][PARENTS[host]]['gm']['value']), a_host_km=a_host,
            reference_radius_km=radii[0]-design.submoon_radius_km,
            submoon_radius_km=design.submoon_radius_km, eccentricity_max=0.3, contact_buffer=0.02)
        host_period = 2*math.pi*math.sqrt(a_host**3/(domain.gm_parent+domain.gm_host))
        setups[host] = gms, figures, radii, domain, host_period
        for radial in ('inner', 'outer'):
            for inclination in (0, 90, 180):
                for e in (0.0, 0.3):
                    a = domain.inner_km*1.1/(1-e) if radial == 'inner' else domain.hill_km*0.8
                    probe = elements_to_cartesian(float(gms[0]), a, e,
                        math.cos(math.radians(inclination)), 0., 0., 0., basis)
                    cases.append(dict(orbit_id=f'{host}-{radial}-{inclination}-{e}', host=host,
                        radial=radial, inclination_degrees=inclination, eccentricity=e, a_km=a,
                        period_seconds=2*math.pi*math.sqrt(a**3/gms[0]),
                        state=np.vstack([relative, probe]).ravel().tolist()))
    write_json(run.folder/'initial_conditions/contrasts.json', dict(horizon_seconds=design.horizon_seconds,
        epoch_jd_tdb=2451545.0, frame='ICRF', time_scale='TDB', units='km,km/s,s', records=cases,
        sample_kind='development_contrasts_not_probability_sample'))
    checks = {}
    for item in cases:
        run.check_budget()
        gms, figures, radii, domain, host_period = setups[item['host']]
        tracker = EscapeTracker(domain.hill_km, host_period)
        started, cpu = time.perf_counter(), time.process_time()
        latest = dict(time=0., state=item['state'], steps=0, nfev=0, escape=tracker.snapshot())
        def progress(data):
            latest.update(data)
        def budget():
            run.check_budget()
            if time.perf_counter()-started >= seconds_per_case:
                raise TimeoutError('Лимит контрастного случая; не физическая потеря')
        status, outcome, result, error = 'partial', 'unresolved', None, None
        try:
            result = integrate(item['state'], gms, figures, radii, design.horizon_seconds,
                rtol=config['rtol'], atol_position=config['atol_position_km'],
                atol_velocity=config['atol_velocity_km_s'],
                max_step=item['period_seconds']*config['max_step_period_fraction'],
                escape_tracker=tracker, progress=progress, budget=budget)
            status, outcome = 'completed', result['physical_outcome']
        except TimeoutError as exc:
            error = str(exc)
        wall = time.perf_counter()-started
        row = dict(orbit_id=item['orbit_id'], host=item['host'], radial=item['radial'],
            inclination_degrees=item['inclination_degrees'], eccentricity=item['eccentricity'],
            horizon_seconds=design.horizon_seconds, run_status=status, physical_outcome=outcome,
            last_valid_time=latest['time'], wall_seconds=wall, cpu_seconds=time.process_time()-cpu,
            nfev=latest['nfev'], steps=latest['steps'], error=error,
            extrapolated_full_horizon_seconds=wall*design.horizon_seconds/latest['time'] if latest['time'] > 0 else None,
            extrapolation_validated=False, terminal_event=result['event'] if result else None)
        costs.append(row)
        write_json(run.folder/f"checkpoints/{item['orbit_id']}.json", dict(
            purpose='partial_state_cost_evidence_not_validated_production_restart',
            model_sha256=run.ledger.used[config['model_path']], horizon_seconds=design.horizon_seconds,
            initial_state=item['state'], **latest))
        write_json(run.folder/'results/costs.json', costs)
        checks[item['orbit_id']] = bool(latest['time'] > 0 and wall > 0 and latest['steps'] > 0)
    partial = [x['extrapolated_full_horizon_seconds'] for x in costs
               if x['run_status'] == 'partial' and x['extrapolated_full_horizon_seconds'] is not None]
    count = len(design.development)*len(design.measures)*design.randomizations*2**design.power
    summary = dict(benchmark_cases=len(cases), completed=sum(x['run_status'] == 'completed' for x in costs),
        partial=sum(x['run_status'] == 'partial' for x in costs), pilot_integration_count=count,
        horizon_seconds=design.horizon_seconds, variance_evidence=None,
        cost_extrapolation_kind='conditional_on_persistence_of_short_segment_rate_not_forecast',
        per_orbit_seconds_quantiles=np.quantile(partial, [0, .5, .9, 1]).tolist() if partial else None,
        hypothetical_all_survive_pilot_hours=float(np.median(partial)*count/3600) if partial else None,
        production_allowed=False, full_pilot_allowed=False,
        limitation='Контрастные короткие участки не измеряют время до потери и долю долгоживущих орбит.')
    write_json(run.folder/'results/summary.json', summary)
    run.manifest['resources']['integration_calls'] = len(costs)
    run.manifest['scientific_admission'] = dict(audited_physics=True, validated_generator=True,
        validated_dynamics=False, validated_events=False, full_horizon_completed=False)
    run.validate(checks, scope='short_segment_cost_only_not_W1_pilot_or_long_W2')
    return summary
