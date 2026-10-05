"""??????? offline-??????? L1 v0.2, ??? ??????? ??????????."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from submoon_research.contracts.l1 import validate_spec, validate_states
from submoon_research.execution import RunContext
from submoon_research.workflows.l1_specification import verify_l1_contract
from submoon_research.workflows.smoke import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('configs/experiments/L1_baseline_spec_v0_3.yaml'))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, 'W0-L1', wall_seconds=120, output_mib=64)
    try:
        with run:
            run.save_code(Path(__file__).resolve())
            run.save_code(root / 'src/submoon_research/contracts/l1_v0_2.json')
            ledger = run.ledger
            spec = ledger.yaml(args.config, registered=True)
            run.save_config(spec)
            validate_spec(spec)
            run.save_code(root / ('src/submoon_research/contracts/l1_v'+spec['schema_version'].replace('.','_')+'.json'))
            for path, digest in spec['inputs_sha256'].items():
                ledger.bind(path, digest)
            sources = ledger.json('references/manifests/L1_sources_v'+spec['schema_version'].replace('.','_')+'.json', registered=True)
            ledger.bind_manifest(sources)
            route = spec['routes']['independent_reproduction']
            states = ledger.json(route['states']['path'])
            validate_states(states, spec)
            passports = {}
            for host, entry in route['hosts'].items():
                ledger.bind(entry['passport'], entry['passport_sha256'])
                passport = ledger.yaml(entry['passport'])
                if passport.get('host_id') != host:
                    raise ValueError(f'??????? ??????? ???????: {host}')
                passports[host] = passport
            ledger.verify_all()
            checks, comparison = verify_l1_contract(spec, states, passports,
                ledger.text('data/raw/W0-himalia-sources-20261004/jup344.cmt'),
                ledger.text('data/raw/W0-l1-sources-20261004/jup347.cmt'))
            old_spec = ledger.yaml(spec['routes']['archive_literal']['source_specification'])
            checks['archive_contract_unchanged'] = all(spec['routes']['archive_literal'][key] == old_spec[key]
                for key in ('units','epoch','frame','physics','initial_measure','integration','events'))
            checks['source_hashes'] = True
            validation = run.validate(checks, scope='schema_provenance_and_scientific_contract_only',
                layers=dict(schema=checks['strict_spec_schema'], provenance=True, scientific_states=checks['scientific_states'],
                    dynamics='not_run'), production_allowed=False, integration_allowed=False,
                limitations=['????/???????/?????????? ?? ??????????????; W0/W1/W2 ?? ???????.',
                    '???????? ??????? ? ??????????? ????????? ???????????.'])
            write_json(run.folder / 'results/ephemeris_comparison.json', comparison)
        print(json.dumps(dict(run_id=run.run_id, validation=validation['status'], checks=checks), ensure_ascii=False))
        return int(validation['status'] != 'passed')
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
        print(json.dumps(dict(run_id=run.run_id, validation=run.manifest['validation']['status'], error=str(exc)),ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
