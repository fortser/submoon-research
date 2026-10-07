"""Независимые synthetic-контроли покрытия W1; не динамический пилот."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
import numpy as np
from scipy.stats import qmc, beta

from submoon_research.execution import RunContext
from submoon_research.sampling.inference import bounded_interval, required_randomizations
from submoon_research.sampling.statistics import randomized_interval
from submoon_research.workflows.smoke import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    config = dict(experiment_id='W1-coverage-v1', data_kind='synthetic_analytic_control',
                  repetitions=256, scrambles=8, power=8, production_allowed=False,
                  seed_development=72400000, seed_validation=82500000,
                  method='hoeffding_bonferroni', student_t_admitted=False,
                  acceptance_one_sided_95_lower=0.94)
    with RunContext(root, 'W1-coverage', config, wall_seconds=120, output_mib=16) as run:
        run.save_code(Path(__file__).resolve())
        checks, report = {}, {}
        truth = np.array([0.5, 0.5, 0.0001, 0.0, 1.0])
        names = ['smooth', 'diagonal_boundary', 'rare', 'zero', 'one']
        for phase in ('development', 'validation'):
            coverage = np.zeros((2, len(names)), dtype=int)
            for repetition in range(config['repetitions']):
                run.check_budget()
                means = []
                for k in range(config['scrambles']):
                    seed = config['seed_'+phase]+repetition*config['scrambles']+k
                    points = qmc.Sobol(2, scramble=True, seed=seed).random_base2(config['power'])
                    x, y = points.T
                    means.append([float(x.mean()), float((x+y < 1).mean()), float((x < 0.0001).mean()), 0., 1.])
                for j in range(len(names)):
                    values = np.asarray(means)[:, j]
                    guaranteed = bounded_interval(values)
                    approx = randomized_interval(values)['interval']
                    coverage[0, j] += guaranteed['lower'] <= truth[j] <= guaranteed['upper']
                    coverage[1, j] += approx[0] <= truth[j] <= approx[1]
            report[phase] = {}
            for j, name in enumerate(names):
                count = int(coverage[0, j])
                lower = float(beta.ppf(0.05, count, config['repetitions']-count+1)) if count else 0.
                report[phase][name] = dict(truth=float(truth[j]), repetitions=config['repetitions'],
                    covered=count, coverage_lower_95=lower,
                    student_t_covered=int(coverage[1, j]), student_t_admitted=False)
                checks[phase+'_'+name] = lower >= config['acceptance_one_sided_95_lower']
        report['precision'] = dict(guaranteed_scrambles_single_fraction=required_randomizations(),
            guaranteed_scrambles_30_fractions=required_randomizations(comparisons=30),
            nominal_pilot_scrambles=8, actual_dynamical_variance=None,
            warning='Это достаточная консервативная граница, не измеренный оптимальный бюджет.')
        write_json(run.folder/'results/coverage.json', report)
        validation = run.validate(checks, scope='V06_intervals_synthetic_only',
            student_t_admitted=False, dynamics_validated=False)
    print(json.dumps(dict(run_id=run.run_id, status=validation['status'])))
    return int(validation['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
