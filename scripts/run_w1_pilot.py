"""Пилот W1 только после численного и ресурсного допуска; обхода ворот нет."""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.catalog.nominal_model import validate_model
from submoon_research.contracts.nominal_states import validate_nominal_states
from submoon_research.contracts.w1 import W1Design
from submoon_research.dynamics.initialization import physical_inputs, PARENTS
from submoon_research.execution import RunContext
from submoon_research.provenance import InputLedger
from submoon_research.sampling.design import DomainSpec
from submoon_research.workflows.w1_ensemble import execute, validate_pilot_admission


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admission', type=Path, required=True, help='Проверенный JSON допуска W2 и стоимости')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    ledger = InputLedger(root)
    design = W1Design.model_validate(ledger.yaml('configs/sampling/W1_design_v1.yaml', registered=True))
    admission = ledger.json(args.admission, registered=True)
    resources = ledger.yaml('configs/resources.yaml', registered=True)
    validate_pilot_admission(admission, design, resources)
    with RunContext(root, 'W1-pilot', wall_seconds=resources['pilot_wall_seconds'],
                    output_mib=resources['pilot_output_mib']) as run:
        run.save_code(Path(__file__).resolve())
        for name, digest in ledger.used.items():
            run.ledger.bind(name, digest)
            run.ledger.read(name)
        source = run.ledger.yaml('configs/experiments/W0_nominal_benchmark_v1.yaml', registered=True)
        for path, digest in source['inputs_sha256'].items():
            run.ledger.bind(path, digest)
        model = validate_model(run.ledger.json(source['model_path']))
        states = validate_nominal_states(run.ledger.json(source['states_path']))
        run.save_config(dict(experiment_id=design.design_id+'-pilot', design=design.model_dump(),
            admission=admission, data_kind='real_nominal_development_pilot', production_allowed=False))
        setups = {}
        for host in design.development:
            names, relative, gms, figures, radii, a_host, basis = physical_inputs(host, model, states)
            domain = DomainSpec(domain_id=host+'-nominal-v1', gm_host=float(gms[0]),
                gm_parent=model['bodies'][PARENTS[host]]['gm']['value'], a_host_km=a_host,
                reference_radius_km=radii[0]-design.submoon_radius_km,
                submoon_radius_km=design.submoon_radius_km,
                eccentricity_max=design.eccentricity_max, contact_buffer=design.contact_buffer)
            setups[host] = dict(domain=domain, relative=relative, gms=gms, figures=figures,
                radii=radii, basis=basis, host_period=2*math.pi*math.sqrt(a_host**3/(domain.gm_parent+gms[0])))
        execute(run, design, setups, admission)
    print(json.dumps(dict(run_id=run.run_id, status=run.manifest['validation']['status'])))
    return int(run.manifest['validation']['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
