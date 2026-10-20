"""OPT-06: создать независимый артефакт P2 inputs или отдельно проверить генератор.

Историческая реконструкция требует внешних SHA summary/jobs и совпадения КАЖДОГО
ожидаемого job fingerprint с историей. Исходный run никогда не дополняется.
"""
from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import admission_p2_2 as stand  # noqa: E402
from submoon_research.contracts.frozen_p2 import (  # noqa: E402
    check_generator, load_frozen_inputs, save_frozen_inputs,
)
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, contract_digest, file_digest, overlaps, read_job_rows, read_json,
)
from submoon_research.exact_linalg import LEGACY, NUMERICS_VERSION, numerics  # noqa: E402


def document(params, implementations, scheme):
    with numerics(scheme):
        doc = stand.units_document(stand.units(params['per_cell'], params['unit_start']), params, implementations)
    doc['generator_numerics'] = scheme
    doc['contract_sha256'] = contract_digest(doc)
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--output-dir', required=True)
    p.add_argument('--per-cell', type=int, default=1)
    p.add_argument('--b-per-cell', type=int, default=1)
    p.add_argument('--unit-start', type=int, default=0)
    p.add_argument('--years', type=float, nargs='+', default=[0.0003])
    p.add_argument('--b-years', type=float, nargs='*', default=[0.0003])
    p.add_argument('--source-run')
    p.add_argument('--source-summary-sha256')
    p.add_argument('--source-jobs-sha256')
    p.add_argument('--check-generator', metavar='INPUTS_MANIFEST')
    p.add_argument('--manifest-sha256')
    args = p.parse_args(argv)
    out = (ROOT/args.output_dir).resolve()
    if out.exists():
        raise ValueError('Нужен новый каталог артефакта')
    if args.check_generator:
        source = ROOT/args.check_generator
        if overlaps(out, source.parent):
            raise ValueError('Выход не должен пересекать frozen inputs')
        report = check_generator(source, args.manifest_sha256, lambda d: document(
            d['params'], d['contract']['implementations'], d['generator_numerics']))
        out.mkdir(parents=True)
        atomic_json(out/'generator_check.json', report)
        print(report)
        return 0 if report['passed'] else 1
    params = stand.normalize_params(args.per_cell, args.b_per_cell, args.unit_start, args.years, args.b_years)
    provenance = dict(kind='generated', python=platform.python_version(),
                      packages={n: importlib.metadata.version(n) for n in ('numpy', 'scipy')},
                      code_sha256={n: file_digest(ROOT/n) for n in (
                          'scripts/freeze_p2_inputs.py', 'scripts/admission_p2_2.py',
                          'src/submoon_research/sampling/design.py',
                          'src/submoon_research/dynamics/initialization.py',
                          'src/submoon_research/exact_linalg.py')})
    if args.source_run:
        source = (ROOT/args.source_run).resolve()
        if overlaps(out, source):
            raise ValueError('Выход не должен пересекать исходный run')
        if (file_digest(source/'summary.json') != args.source_summary_sha256
                or file_digest(source/'jobs.jsonl') != args.source_jobs_sha256):
            raise ValueError('SHA исторического источника не совпал с внешним якорем')
        summary = read_json(source/'summary.json')
        impl = summary['implementations']
        scheme = summary.get('generator_numerics', LEGACY)
        doc = document(params, impl, scheme)
        # Reader проверяет весь файл источника; затем точная сверка выбранных заданий.
        from submoon_research.contracts.p2_files import loads
        raw = [loads(line) for line in (source/'jobs.jsonl').read_text(encoding='utf-8').splitlines()]
        source_fp = {(r['orbit_id'], r['years'], r['variant']): r['job_fingerprint'] for r in raw}
        rows = read_job_rows(source/'jobs.jsonl', source_fp)
        by_id = {u['orbit_id']: u for u in doc['units']}
        keys = [k for group in stand.plan_keys(doc['units'], params).values() for k in group]
        for key in keys:
            if key not in rows or stand.job_fingerprint(by_id[key[0]], key[1], key[2], impl) != rows[key]['job_fingerprint']:
                raise ValueError('Не воспроизведён исторический fingerprint: '+str(key))
        provenance.update(kind='historical-fingerprint-reconstruction', source_run=args.source_run,
                          source_summary_sha256=args.source_summary_sha256,
                          source_jobs_sha256=args.source_jobs_sha256, matched_jobs=len(keys),
                          limitation='Реконструкция по закреплённой истории, не новые независимые старты')
    else:
        impl = dict(compiled=stand.implementation_fingerprint(), B=stand.b_fingerprint())
        doc = document(params, impl, NUMERICS_VERSION)
    sha = save_frozen_inputs(out, doc, provenance)
    load_frozen_inputs(out/'inputs_manifest.json', sha, params=params)
    print('INPUTS MANIFEST:', out/'inputs_manifest.json')
    print('PIN THIS SHA-256 IN AN INDEPENDENT RECORD:', sha)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
