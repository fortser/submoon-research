"""Общий CLI паспортных проверок: ранний run, SHA источников, единая финализация."""
import argparse
import json
from io import BytesIO
from pathlib import Path

from submoon_research.execution import RunContext


def prepare_passport(run, config):
    ledger = run.ledger
    sources = ledger.json(config['source_manifest'], registered=True)
    ledger.bind_manifest(sources)
    # source_paths обязаны иметь ожидаемый SHA из первичного манифеста.
    readings = {key:ledger.text(path) for key,path in config['source_paths'].items()}
    passport = ledger.yaml(config['passport'], registered=True)
    active = ledger.read(config['active_passport'], registered=True)
    if active != ledger.read(config['passport'], registered=True):
        raise ValueError('Активный паспорт отличается от версионированного')
    ledger.bind(config['baseline_archive'], config['baseline_archive_sha256'])
    archive = BytesIO(ledger.read(config['baseline_archive']))
    for key in ('au_source_path', 'paper_path'):
        if key in config:
            registry=ledger.json('references/source_manifest.json', registered=True)
            for entry in registry['sources']:
                if entry.get('path') == config[key] and entry.get('sha256'):
                    ledger.bind(entry['path'], entry['sha256'])
            ledger.read(config[key], registered=True)
    ledger.verify_all()
    return passport, readings, archive


def audit_cli(script, prefix, default_config, execute):
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path(default_config))
    args = parser.parse_args()
    root = Path(script).resolve().parents[1]
    run = RunContext(root, prefix, wall_seconds=120, output_mib=64)
    try:
        with run:
            run.save_code(Path(script).resolve())
            config = run.ledger.yaml(args.config, registered=True)
            run.save_config(config)
            limits = config.get('validation_limits', {})
            run.wall_seconds = min(run.wall_seconds, limits.get('max_wall_seconds', run.wall_seconds))
            run.output_bytes = min(run.output_bytes, limits.get('max_output_mib', 64)*2**20)
            checks, metrics = execute(run, config)
        print(json.dumps(dict(run_id=run.run_id, validation=run.manifest['validation']['status'], checks=checks, metrics=metrics),ensure_ascii=False))
        return int(run.manifest['validation']['status'] != 'passed')
    except (ValueError,OSError,KeyError,TypeError,RuntimeError,TimeoutError) as exc:
        print(json.dumps(dict(run_id=run.run_id,error=str(exc)),ensure_ascii=False))
        return 1
