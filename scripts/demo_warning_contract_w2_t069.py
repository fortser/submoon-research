"""Демонстрация warning-политики на коротком синтетическом IAS15 кеше."""
from __future__ import annotations

import hashlib
import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.diagnostics import warning_contract_v1 as contract  # noqa: E402
from submoon_research.dynamics.native_ias15 import MassiveCache  # noqa: E402

OUT = ROOT / 'results/evidence/W2_T069_warning_policy_20261030/native_demo_v2.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise SystemExit('Новый выход не должен существовать')
    impl = ROOT / 'src/submoon_research/diagnostics/warning_contract_v1.py'
    fp = contract.policy_fingerprint(sha(impl), sha(Path(__file__)))
    # Однопроцессный демонстрационный захват вокруг всего вызова кеша;
    # для production такая конструкция не доказывает полноту дочерних процессов.
    cache = MassiveCache([[0, 0, 0, 0, 0, 0], [1, 0, 0, 0, 1, 0]], [1.], [], epsilon=1e-13)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter('always')
        cache.extend(2., lambda: None)
    items = [dict(category=r.category.__name__, message=str(r.message), phase='cache', time=None) for r in recorded]
    ledger = contract.assess(engine_status='completed', capture_complete=True, overflow=False,
                             events=items, policy_sha256=fp)
    if not items or ledger['assessment']['admissible']:
        raise RuntimeError('Ожидалось наблюдаемое предупреждение и блокировка')
    doc = dict(synthetic=True, policy_prototype_only=True, python=sys.version, ledger=ledger,
               ledger_sha256=contract.digest(ledger), engine_end=cache.end,
               module_sha256=sha(impl), capture_sha256=sha(Path(__file__)),
               limitation='Только синтетический однопроцессный вызов cache.extend; не production capture.')
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(doc, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
