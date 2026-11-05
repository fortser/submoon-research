"""W2-T065: ретроспективное сравнение по проверенному снимку; без интеграций/допуска."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from submoon_research.diagnostics import convergence_v2_guard as guard  # noqa: E402
from submoon_research.diagnostics.phase3_v2_input import load  # noqa: E402

BASELINE = ROOT / 'results/evidence/W2_phase3_audit_20261030/v1/audit.json'
BASELINE_SHA = '0febe080e713cca4ea7cd6530beb46b7a89aa3f3fcff9b6bb17d69769e236150'
ORIGINAL = ROOT / 'results/evidence/W2_phase3_analysis_20261028_pi/analysis.json'
SOURCES = ('scripts/compare_phase3_v1_v2.py', 'src/submoon_research/diagnostics/phase3_v2_input.py',
           'src/submoon_research/diagnostics/phase3_audit.py', 'src/submoon_research/diagnostics/convergence.py',
           'src/submoon_research/diagnostics/convergence_v2_guard.py')


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analyze(data, old):
    previous = {item['orbit_id']: item for item in old['cases']}
    if len(previous) != len(old['cases']) or len(previous) != len(data['cases']):
        raise ValueError('Исторический список случаев не совпадает')
    rows = []
    for item in data['cases']:
        oid = item['orbit_id']
        historical = previous[oid]['classification']
        result = guard.classify(item['case'], data['thresholds'], data['directions'], item['archive_b'])
        old_local = result['original']
        if historical != old_local:
            raise ValueError('Классификация v1 не воспроизведена точно: ' + oid)
        rows.append(dict(orbit_id=oid, role=item['role'], high_e=item['case']['high_e'],
            v1_class=historical['cls'], v1_confidence=historical['confidence'],
            v2_class=result['cls'], v2_confidence=result['confidence'],
            flags=result['flags'], label_nonconvergence=result['label_nonconvergence'],
            perturbation_grid=result['perturbation_grid'], physical_sensitivity=result['physical_sensitivity'],
            archive_b_present=item['archive_b'] is not None,
            v1_wrong=historical.get('wrong'), v1_reason=historical.get('reason')))
    return dict(cases=rows, transitions=dict(sorted(Counter(
        f"{r['v1_class']}->{r['v2_class']}" for r in rows).items())),
        confidence_changes=[r['orbit_id'] for r in rows if r['v1_confidence'] != r['v2_confidence']],
        note='v2 — неполный разработочный прототип; K0/K2 сохранены v1 и отдельно не подтверждены; '
             'общий выбор режима не пересчитан и не является допуском')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    results = (ROOT / 'results/evidence').resolve()
    if output.exists() or not output.is_relative_to(results):
        raise SystemExit('Нужен новый каталог внутри results/evidence')
    code_before = {p: digest(ROOT / p) for p in SOURCES}
    data = load(ROOT, BASELINE, BASELINE_SHA)
    # Оригинал присутствует в зафиксированном инвентаре; сравнить с ним и после чтения.
    original_sha = digest(ORIGINAL)
    pinned = json.loads(BASELINE.read_text(encoding='utf-8'))['inventory'][ORIGINAL.relative_to(ROOT).as_posix()]['sha256']
    if digest(BASELINE) != BASELINE_SHA or original_sha != pinned:
        raise ValueError('Исторический анализ не совпадает с закреплённым инвентарём')
    old = json.loads(ORIGINAL.read_text(encoding='utf-8'))
    doc = analyze(data, old)
    if digest(ORIGINAL) != original_sha or code_before != {p: digest(ROOT / p) for p in SOURCES}:
        raise ValueError('Код или исторический анализ изменился во время разбора')
    doc.update(schema='w2-phase3-v1-v2-retrospective-v1', utc=datetime.now(timezone.utc).isoformat(),
        baseline_sha256=BASELINE_SHA, inventory_sha256=data['inventory_sha256'], original_sha256=original_sha,
        source_sha256=code_before, python=platform.python_version(), numpy=np.__version__,
        input_rows=data['rows'], runs=data['runs'], decision='W2-D047', production_allowed=False)
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'analysis.json').open('x', encoding='utf-8') as stream:
        json.dump(doc, stream, ensure_ascii=False, indent=2, allow_nan=False)
    with (output / 'cases.csv').open('x', encoding='utf-8', newline='') as stream:
        fields = ['orbit_id', 'role', 'high_e', 'v1_class', 'v1_confidence', 'v2_class', 'v2_confidence',
                  'flags', 'label_nonconvergence', 'archive_b_present']
        w = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        for row in doc['cases']:
            w.writerow(dict(row, flags=';'.join(row['flags'])))
    print(json.dumps(dict(transitions=doc['transitions'], confidence_changes=doc['confidence_changes'],
                          output=str(output), output_sha256=digest(output / 'analysis.json')), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
