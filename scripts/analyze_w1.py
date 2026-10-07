"""Повторный анализ разработочного ансамбля W1 по проверенному манифесту."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.contracts.w1 import W1Design
from submoon_research.execution import RunContext
from submoon_research.sampling.inference import aggregate, bounded_interval
from submoon_research.workflows.smoke import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot-run', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with RunContext(root, 'W1-analysis', dict(data_kind='development_analysis'), wall_seconds=120, output_mib=32) as run:
        run.save_code(Path(__file__).resolve())
        manifest = run.ledger.json(args.pilot_run/'manifest.json', registered=True)
        if manifest['data_kind'] != 'real_nominal_development_pilot':
            raise ValueError('Контрастный benchmark не является оценочной выборкой')
        for path in ('results/outcomes.json', 'config.resolved.yaml'):
            run.ledger.bind(args.pilot_run/path, manifest['artifacts_sha256'][path])
        config = run.ledger.yaml(args.pilot_run/'config.resolved.yaml')
        design = W1Design.model_validate(config['design'])
        rows = run.ledger.json(args.pilot_run/'results/outcomes.json')
        estimates = aggregate(rows, design)
        write_json(run.folder/'results/estimates.json', [dict(host=h, measure=m,
            interval=bounded_interval(lo, hi, comparisons=len(estimates)),
            randomization_lower=lo.tolist(), randomization_upper=hi.tolist())
            for (h, m), (lo, hi) in estimates.items()])
        run.validate(dict(complete_denominator=True), scope='development_analysis_not_H1_H3_validation')
    print(json.dumps(dict(run_id=run.run_id, status='passed')))


if __name__ == '__main__':
    main()
