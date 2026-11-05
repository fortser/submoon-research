"""Изолировать чтение analyze_w1; остановиться ДО aggregate/научных вычислений."""
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
import submoon_research.execution as execution  # noqa: E402
from submoon_research.contracts.w1 import W1Design  # noqa: E402

spec = importlib.util.spec_from_file_location('analyze_w1_probe', ROOT / 'scripts/analyze_w1.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReachedAggregation(Exception):
    pass


def stop_before_aggregation(*args, **kwargs):
    raise ReachedAggregation('synthetic boundary; aggregate NOT executed')


def probe(version, snapshot, *, bad_sha=False, failed_status=False):
    with tempfile.TemporaryDirectory(prefix='w2_t073_reader_') as temporary:
        root = Path(temporary)
        package = root / 'src/submoon_research'
        package.mkdir(parents=True)
        (package / 'execution.py').write_bytes(b'# synthetic package\n')
        script = root / 'scripts/analyze_w1.py'
        script.parent.mkdir()
        script.write_bytes(b'# synthetic launcher path; actual function imported separately\n')
        (root / 'configs').mkdir()
        (root / 'configs/resources.yaml').write_text(yaml.safe_dump(dict(
            pilot_wall_seconds=600, pilot_output_mib=256, minimum_free_disk_gib=0,
            threads_per_worker=1)), encoding='utf-8')
        pilot = root / 'runs/synthetic-pilot'
        (pilot / 'results').mkdir(parents=True)
        (pilot / 'code').mkdir()
        (pilot / 'code/a.py').write_bytes(b'# synthetic input code\n')
        config = yaml.safe_dump({'design': W1Design(design_id='synthetic-reader-probe').model_dump()}).encode()
        (pilot / 'config.resolved.yaml').write_bytes(config)
        (pilot / 'results/outcomes.json').write_bytes(b'[]')
        manifest = dict(schema_version=version, status='failed' if failed_status else 'completed',
            validation={'status': 'not_run' if failed_status else 'passed'},
            data_kind='real_nominal_development_pilot', run_id='synthetic-pilot',
            artifacts_sha256={'config.resolved.yaml': hashlib.sha256(config).hexdigest(),
                              'results/outcomes.json': '0'*64 if bad_sha else hashlib.sha256(b'[]').hexdigest()},
            code_sha256={'a.py': hashlib.sha256((pilot / 'code/a.py').read_bytes()).hexdigest()})
        if snapshot is not None:
            manifest['initial_code_snapshot'] = snapshot
        (pilot / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with patch.object(module, '__file__', str(script)), \
                patch.object(execution, '__file__', str(package / 'execution.py')), \
                patch.object(module, 'aggregate', stop_before_aggregation), \
                patch.object(sys, 'argv', ['analyze_w1.py', '--pilot-run', 'runs/synthetic-pilot']):
            try:
                module.main()
            except ReachedAggregation:
                return 'reached_aggregation_boundary_without_executing_it'
            except Exception as exc:
                return type(exc).__name__ + ': ' + str(exc)
        raise AssertionError('Неожиданное завершение без synthetic boundary')


complete = dict(state='complete', expected_paths=['a.py'], published_count=1)
cases = {
    'legacy_0.2': ('0.2', None, {}),
    'complete_0.3': ('0.3', complete, {}),
    'pending_0.3': ('0.3', dict(state='pending', expected_paths=['a.py'], published_count=0), {}),
    'missing_expected': ('0.3', dict(state='complete', expected_paths=['a.py', 'missing.py'], published_count=2), {}),
    'wrong_count': ('0.3', dict(state='complete', expected_paths=['a.py'], published_count=99), {}),
    'failed_status': ('0.3', complete, {'failed_status': True}),
    'bad_artifact_sha': ('0.3', complete, {'bad_sha': True}),
}
results = {name: probe(version, snapshot, **options) for name, (version, snapshot, options) in cases.items()}
print(json.dumps(dict(data_kind='synthetic_reader_characterization', aggregate_executed=False,
                      results=results), ensure_ascii=False, indent=2))
assert all(value == 'reached_aggregation_boundary_without_executing_it' for key, value in results.items()
           if key != 'bad_artifact_sha')
assert results['bad_artifact_sha'].startswith('ValueError: SHA-256')
