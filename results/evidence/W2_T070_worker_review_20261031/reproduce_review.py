"""Синтетические воспроизведения дефектов; не исправление и не научный запуск.

Запуск из корня: .venv/Scripts/python.exe results/evidence/
W2_T070_worker_review_20261031/reproduce_review.py
Все изменяемые артефакты изолированы в TemporaryDirectory.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.diagnostics import warning_worker_demo as w  # noqa: E402


def main():
    results = {}
    contract = 'demo-fingerprint'
    with tempfile.TemporaryDirectory(prefix='w2-t070-review-') as tmp:
        root = Path(tmp)
        parent = root / 'parent'
        w.worker(parent, contract, 'partial_warning')
        cp_path = parent / 'checkpoint.json'
        pinned_bytes = cp_path.read_bytes()
        pinned_sha = w.sha(pinned_bytes)
        clean_cp = json.loads(pinned_bytes)
        clean_cp['events'] = []
        original_read = w.read_json

        def replace_before_parse(path):
            if path == cp_path:
                path.write_bytes(w.encode(clean_cp))
            return original_read(path)

        # Детерминированная имитация замены между read_bytes и read_json.
        with patch.object(w, 'read_json', side_effect=replace_before_parse):
            verified = w.verify_checkpoint(cp_path, pinned_sha, contract)
        results['checkpoint_toctou'] = {
            'pinned_warning_count': len(json.loads(pinned_bytes)['events']),
            'returned_warning_count': len(verified['events']),
            'current_sha_matches_pin': w.sha(cp_path.read_bytes()) == pinned_sha,
        }
        assert verified['events'] == []
        assert w.sha(cp_path.read_bytes()) != pinned_sha

        # Имитация публикации конкурентом после проверки отсутствия файла.
        destination = root / 'exclusive.json'
        real_exists = Path.exists

        def raced_exists(path):
            if path == destination:
                path.write_bytes(b'{"owner":"other"}')
                return False
            return real_exists(path)

        with patch.object(Path, 'exists', raced_exists):
            w.write_atomic(destination, {'owner': 'this'})
        results['exclusive_publish_race'] = original_read(destination)
        assert results['exclusive_publish_race'] == {'owner': 'this'}

        clean = root / 'clean'
        w.worker(clean, contract, 'clean')
        receipt_path = clean / 'receipt.json'
        receipt = original_read(receipt_path)
        receipt['warning_count'] = False
        receipt_path.write_bytes(w.encode(receipt))
        verdict = w.evaluate(clean, contract, 0, b'', b'')
        results['boolean_warning_count'] = verdict
        assert verdict['admissible']

        # Внешней идентичности job нет в интерфейсе; clean каталог и тот же
        # contract принимаются независимо от того, какое задание ожидалось.
        results['unbound_job_directory'] = w.evaluate(clean, contract, 0, b'', b'')
        assert results['unbound_job_directory']['admissible']

        malformed = dict(clean_cp, events=[{'unexpected': 'not a warning record'}])
        cp_path.write_bytes(w.encode(malformed))
        accepted = w.verify_checkpoint(cp_path, w.sha(cp_path.read_bytes()), contract)
        results['malformed_parent_event'] = {'accepted_events': accepted['events']}
        assert accepted['events'] == malformed['events']

    targets = [
        'src/submoon_research/diagnostics/warning_worker_demo.py',
        'src/submoon_research/diagnostics/warning_contract_v1.py',
        'tests/unit/test_warning_worker_demo.py',
        'tests/unit/test_warning_contract_v1.py',
    ]
    print(json.dumps({
        'scope': 'synthetic review probes, no production admission',
        'python': sys.version,
        'reviewed_sha256': {
            path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in targets
        },
        'results': results,
    }, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
