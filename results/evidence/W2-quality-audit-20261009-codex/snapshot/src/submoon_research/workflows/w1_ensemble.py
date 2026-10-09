"""Прямой пилот W1: полный план стартов, ограниченное исполнение и единый знаменатель."""
import math
import time

import numpy as np

from submoon_research.contracts.w1 import EnsembleRow
from submoon_research.dynamics.baseline import integrate
from submoon_research.events.escape import EscapeTracker
from submoon_research.sampling.design import generate
from submoon_research.sampling.inference import aggregate, bounded_interval
from submoon_research.tracking import atomic_text
from submoon_research.workflows.smoke import write_json
import json


def validate_pilot_admission(admission, design, resources):
    if (admission.get('status') != 'validated' or admission.get('design_id') != design.design_id
            or admission.get('horizon_seconds') != design.horizon_seconds
            or admission.get('integrator') != 'DOP853'
            or not all(admission.get(k) is True for k in
                       ('audited_inputs', 'events_validated', 'long_dynamics_validated', 'restart_validated'))):
        raise ValueError('Пилот не допущен: входы/длительная динамика/события/рестарт')
    wall = admission.get('estimated_total_wall_seconds')
    if not isinstance(wall, (int, float)) or not math.isfinite(wall) or not 0 < wall <= resources['pilot_wall_seconds']:
        raise ValueError('Полная серия не укладывается в зарегистрированный профиль ресурсов')
    if not admission.get('evidence_sha256') or not admission.get('numeric_options'):
        raise ValueError('Допуск требует проверенных файлов и численного режима')
    return admission['numeric_options']


def prepare(design, setups, *, budget=lambda: None):
    """setups: проверенные DomainSpec, basis, массивные состояния и параметры сил."""
    if set(setups) != set(design.development):
        raise ValueError('Пилот требует всех трёх разработочных хозяев')
    jobs = []
    for host in design.development:
        setup = setups[host]
        domain = setup['domain']
        for measure in design.measures:
            for k in range(design.randomizations):
                budget()
                sample = generate(domain, measure, power=design.power, seed=design.seed(k),
                    randomization=k, basis=setup['basis'])
                for index, row in enumerate(sample['records']):
                    orbit = EnsembleRow(host=host, scenario_id=design.scenario_id, measure=measure,
                        domain_id=domain.domain_id, randomization=k, seed=design.seed(k), index=index,
                        weight=1., horizon_seconds=design.horizon_seconds, last_valid_time=0.,
                        run_status='partial', outcome='unresolved')
                    jobs.append(dict(orbit_id=f'{host}-{measure}-{k}-{index}',
                        row=orbit.model_dump(),
                        state=np.vstack([setup['relative'], row['position']+row['velocity']]).ravel().tolist(),
                        elements=row['elements'], sampling=row['sampling'],
                        period_seconds=2*math.pi*math.sqrt(row['elements']['a_km']**3/domain.gm_host)))
    return jobs


def execute(run, design, setups, admission):
    options = validate_pilot_admission(admission, design, run.resources)
    for path, digest in admission['evidence_sha256'].items():
        run.ledger.bind(path, digest)
        report = run.ledger.json(path)
        if report.get('status') != 'passed':
            raise ValueError('Проверка численного допуска не прошла')
    jobs = prepare(design, setups, budget=run.check_budget)
    write_json(run.folder/'initial_conditions/ensemble.json', jobs)
    outcomes = [job['row'].copy() for job in jobs]
    write_json(run.folder/'results/outcomes.json', outcomes)
    costs = []
    for index, job in enumerate(jobs):
        setup = setups[job['row']['host']]
        tracker = EscapeTracker(setup['domain'].hill_km, setup['host_period'])
        latest = dict(time=0., state=job['state'])
        def progress(value):
            latest.update(value)
        checkpoint_path = run.folder/'checkpoints'/(job['orbit_id']+'.json')
        def checkpoint(value):
            value.update(orbit_id=job['orbit_id'], design_id=design.design_id)
            atomic_text(checkpoint_path, json.dumps(value))
        started = time.perf_counter()
        error = None
        exhausted = False
        try:
            run.check_budget()
            result = integrate(job['state'], setup['gms'], setup['figures'], setup['radii'],
                design.horizon_seconds, rtol=options['rtol'], atol_position=options['atol_position'],
                atol_velocity=options['atol_velocity'],
                max_step=job['period_seconds']*options['max_step_period_fraction'],
                budget=run.check_budget, progress=progress, escape_tracker=tracker,
                checkpoint_time=design.horizon_seconds/2, checkpoint=checkpoint)
            outcomes[index].update(last_valid_time=result['last_valid_time'], run_status='completed',
                outcome=result['physical_outcome'], event_time=result['event']['time'] if result['event'] else None)
        except (TimeoutError, ArithmeticError, RuntimeError) as exc:
            error = str(exc)
            exhausted = isinstance(exc, TimeoutError)
            outcomes[index].update(last_valid_time=latest['time'], run_status='partial' if isinstance(exc, TimeoutError) else 'failed')
            checkpoint(dict(purpose='partial_not_admitted_for_production_restart', **latest))
        EnsembleRow.model_validate(outcomes[index])
        costs.append(dict(orbit_id=job['orbit_id'], wall_seconds=time.perf_counter()-started,
            cpu_seconds=latest.get('cpu_seconds'), nfev=latest.get('nfev'), error=error))
        atomic_text(run.folder/'results/outcomes.json', json.dumps(outcomes))
        write_json(run.folder/'results/costs.json', costs)
        if exhausted:
            break
    estimates = aggregate(outcomes, design)
    summary = [dict(host=h, measure=m, denominator=design.randomizations*2**design.power,
        randomization_lower=lo.tolist(), randomization_upper=hi.tolist(),
        interval=bounded_interval(lo, hi, comparisons=len(estimates)))
        for (h, m), (lo, hi) in estimates.items()]
    write_json(run.folder/'results/estimates.json', summary)
    run.manifest['scientific_admission'] = dict(audited_physics=True, validated_generator=True,
        validated_dynamics=True, validated_events=True,
        full_horizon_completed=all(r['run_status'] == 'completed' for r in outcomes))
    run.manifest['resources']['integration_calls'] = len(costs)
    sampling = dict(design_id=design.design_id, horizon_seconds=design.horizon_seconds,
        scenario_id=design.scenario_id, randomizations=design.randomizations,
        points_per_randomization=2**design.power, hosts=design.development, measures=design.measures)
    run.manifest['sampling_design'] = sampling
    completed = all(r['run_status'] == 'completed' for r in outcomes)
    resolved = completed and all(r['outcome'] != 'unresolved' for r in outcomes)
    write_json(run.folder/'results/cost_evidence.json', dict(run_id=run.run_id,
        sampling_design=sampling, status='validated' if completed else 'partial', records=costs))
    means = np.array([lo for lo, hi in estimates.values()])
    write_json(run.folder/'results/variance_evidence.json', dict(run_id=run.run_id,
        sampling_design=sampling, status='validated' if resolved else 'partial',
        keys=[list(key) for key in estimates], covariance=np.cov(means, ddof=1).tolist() if resolved else None,
        variance_unit='independent_scramble', unresolved_outcomes=sum(r['outcome'] == 'unresolved' for r in outcomes)))
    run.validate(dict(full_plan_recorded=len(outcomes) == len(jobs), full_horizon_completed=completed,
        all_outcomes_resolved=resolved), scope='real_nominal_development_pilot')
    return outcomes, summary
