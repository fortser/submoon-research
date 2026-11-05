"""Read-only контроль одного фактического завершённого legacy RunContext L0."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.contracts.run_manifest import validate_run_manifest_for_admission  # noqa: E402


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


selected = None
for path in sorted((ROOT / 'runs').glob('L0-*/manifest.json'), reverse=True):
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('schema_version') == '0.2' and value.get('status') == 'completed'
            and value.get('validation', {}).get('status') == 'passed'):
        selected = path, value
        break
if selected is None:
    raise RuntimeError('Фактический завершённый L0 manifest 0.2 не найден')
path, manifest = selected
before = sha(path)
validate_run_manifest_for_admission(manifest)
assert 'initial_code_snapshot' not in manifest
assert manifest['code_sha256'] and manifest['artifacts_sha256']
for name, digest in manifest['code_sha256'].items():
    assert sha(path.parent / 'code' / name) == digest, name
for name, digest in manifest['artifacts_sha256'].items():
    assert sha(path.parent / name) == digest, name
assert sha(path) == before
print(json.dumps(dict(run_id=manifest['run_id'], manifest=path.relative_to(ROOT).as_posix(),
                      manifest_sha256=before, code_count=len(manifest['code_sha256']),
                      artifacts_count=len(manifest['artifacts_sha256']),
                      status='recorded_bytes_verified_read_only',
                      initial_set_completeness='not_proven_for_legacy', real_freeze_executed=False),
                 ensure_ascii=False, indent=2))
