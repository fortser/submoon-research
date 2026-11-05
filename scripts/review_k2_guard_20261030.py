"""Ретроспективный read-only прогон защитного K2 guard по закреплённому снимку."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.diagnostics import convergence_k2_guard_v1 as guard  # noqa: E402
from submoon_research.diagnostics.phase3_v2_input import load  # noqa: E402

BASE = ROOT / 'results/evidence/W2_phase3_audit_20261030/v1/audit.json'
BASE_SHA = '0febe080e713cca4ea7cd6530beb46b7a89aa3f3fcff9b6bb17d69769e236150'
OLD = ROOT / 'results/evidence/W2_T065_v1_v2_20261030_v1/analysis.json'
OUT = ROOT / 'results/evidence/W2_T067_k2_20261030/retrospective.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUT.exists():
        raise SystemExit('Результат существует; выбрать новый каталог')
    old_sha = sha(OLD)
    snapshot = load(ROOT, BASE, BASE_SHA)
    old = {c['orbit_id']: c for c in json.loads(OLD.read_text(encoding='utf-8'))['cases']}
    rows = []
    for item in snapshot['cases']:
        result = guard.classify(item['case'], snapshot['thresholds'], snapshot['directions'], item['archive_b'])
        previous = old[item['orbit_id']]
        if (result['prior_v2_class'], result['original']['cls']) != (previous['v2_class'], previous['v1_class']):
            raise ValueError('Исторические классы не совпадают')
        rows.append(dict(orbit_id=item['orbit_id'], previous=previous['v2_class'], current=result['cls'],
                         k2_status=result['k2_status'], flags=result['flags']))
    if len(rows) != len(old) or sha(OLD) != old_sha or sha(BASE) != BASE_SHA:
        raise ValueError('Вход изменён или не совпадает')
    doc = dict(schema=guard.VERSION, utc=datetime.now(timezone.utc).isoformat(),
               baseline_sha256=BASE_SHA, previous_sha256=old_sha, inventory_sha256=snapshot['inventory_sha256'],
               code_sha256={p: sha(ROOT / p) for p in ('scripts/review_k2_guard_20261030.py',
                   'src/submoon_research/diagnostics/convergence_k2_guard_v1.py')},
               counts=dict(Counter(f"{r['previous']}->{r['current']}" for r in rows)), cases=rows,
               integrations=0, production_allowed=False,
               note='Только повтор просмотренных данных; K2 блокируется, остальные классы не переоценены.')
    with OUT.open('x', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'counts': doc['counts'], 'sha256': sha(OUT)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
