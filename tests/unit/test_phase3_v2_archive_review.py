"""W2-T065: независимые отрицательные проверки архивного B, без интеграций."""
import json
from pathlib import Path

import pytest

from submoon_research.diagnostics import phase3_v2_input as inp

ROOT = Path(__file__).resolve().parents[2]
B = 'runs/W2-B144-57496493'


@pytest.fixture(scope='module')
def archive_snapshot():
    if not all((ROOT / B / name).is_file() for name in ('manifest.json', 'units.json', 'jobs.jsonl')):
        pytest.skip('Архив B144 отсутствует; нужна отдельная проверка восстановленного среза')
    cfg = inp.yaml.safe_load((ROOT / inp.audit.CONFIG).read_text(encoding='utf-8'))
    snapshot = {f'{B}/{name}': (ROOT / B / name).read_bytes()
                for name in ('manifest.json', 'units.json', 'jobs.jsonl')}
    return cfg, snapshot


def test_freeze_rejects_joint_removal_from_manifest_and_jobs(archive_snapshot):
    cfg, original = archive_snapshot
    snapshot = dict(original)
    manifest = inp._json(snapshot[f'{B}/manifest.json'])
    rows = inp._jsonl(snapshot[f'{B}/jobs.jsonl'])
    removed = manifest['plan'].pop()
    rows = [r for r in rows if (r['orbit_id'], r['years']) != (removed['orbit_id'], removed['years'])]
    snapshot[f'{B}/manifest.json'] = json.dumps(manifest).encode()
    snapshot[f'{B}/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    with pytest.raises(ValueError, match='Архивный B: SHA manifest'):
        inp._archive(snapshot, cfg)


def test_freeze_rejects_b_results_changed_without_manifest_change(archive_snapshot):
    cfg, original = archive_snapshot
    snapshot = dict(original)
    rows = inp._jsonl(snapshot[f'{B}/jobs.jsonl'])
    rows[0]['outcome'] = 'survived'
    rows[0]['event'] = None
    rows[0]['last_valid_time'] = 1.0
    snapshot[f'{B}/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    with pytest.raises(ValueError, match='Архивный B: SHA jobs'):
        inp._archive(snapshot, cfg)


@pytest.mark.parametrize('outcome,body', [('host_contact', 1), ('other_contact', 0)])
def test_b_contact_body_mismatch_even_if_digest_allowed(archive_snapshot, outcome, body):
    cfg, original = archive_snapshot
    cfg = dict(cfg, archives={**cfg['archives'], 'b144': dict(cfg['archives']['b144'])})
    snapshot = dict(original)
    rows = inp._jsonl(snapshot[f'{B}/jobs.jsonl'])
    contact = next(r for r in rows if r['outcome'] == 'host_contact')
    contact['outcome'] = outcome
    contact['event']['body'] = body
    snapshot[f'{B}/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    cfg['archives']['b144']['jobs_sha256'] = inp._hash(snapshot[f'{B}/jobs.jsonl'])
    with pytest.raises(ValueError, match='Архивный B: исход/время/событие'):
        inp._archive(snapshot, cfg)


def test_b_survived_early_even_if_digest_allowed(archive_snapshot):
    cfg, original = archive_snapshot
    cfg = dict(cfg, archives={**cfg['archives'], 'b144': dict(cfg['archives']['b144'])})
    snapshot = dict(original)
    rows = inp._jsonl(snapshot[f'{B}/jobs.jsonl'])
    rows[0]['outcome'] = 'survived'
    rows[0]['event'] = None
    rows[0]['last_valid_time'] = 1.0
    snapshot[f'{B}/jobs.jsonl'] = b''.join(json.dumps(r).encode() + b'\n' for r in rows)
    cfg['archives']['b144']['jobs_sha256'] = inp._hash(snapshot[f'{B}/jobs.jsonl'])
    with pytest.raises(ValueError, match='Архивный B: исход/время/событие'):
        inp._archive(snapshot, cfg)
