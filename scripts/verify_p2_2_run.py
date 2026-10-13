"""Строгая приёмка выгруженного run ступени P2-2 (W2-P006 §7): fail-closed, состав — из параметров.

Ожидаемый состав строится заново из параметров утверждённой команды стенда (--per-cell,
--b-per-cell, --unit-start, --years, --b-years) генератором единиц стенда (поток seeds §2):
9 ячеек × единицы × варианты × горизонты. Проверяются: состав без лишних и недостающих
заданий; повтор ключа допустим только поверх остановленной (partial) строки; отпечаток каждого
задания (единица, физика, режим, горизонт, вариант, сетка, SHA реализации из summary);
согласованность каждой строки (статус, время, событие, исход, причина unresolved); совпадение
единицы (хозяин, мера, номер, seed); затем разбор §3 пересчитывается и сверяется с analysis.json
и summary.json. Любое расхождение — провал (код выхода 1).

    python scripts/verify_p2_2_run.py --run runs/W2-P2-2a-<id> --expect-full --expect-complete
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

from verify_p2_run import check_row, read_jsonl  # noqa: E402


def stand_module():
    spec = importlib.util.spec_from_file_location('admission_p2_2', ROOT/'scripts'/'admission_p2_2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key(row):
    return (row['orbit_id'], float(row['years']), row['variant'])


def roundtrip(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=float))


def differences(got, ref, path='', limit=10):
    """Пути, где пересчитанное расходится с сохранённым (первые limit)."""
    out = []
    if isinstance(got, dict) and isinstance(ref, dict):
        for k in sorted(set(got) | set(ref), key=str):
            if k not in got or k not in ref:
                out.append(f'{path}/{k}: есть только в {"пересчёте" if k in got else "сохранённом"}')
            else:
                out += differences(got[k], ref[k], f'{path}/{k}', limit)
            if len(out) >= limit:
                break
    elif isinstance(got, list) and isinstance(ref, list) and len(got) == len(ref):
        for i, (a, b) in enumerate(zip(got, ref)):
            out += differences(a, b, f'{path}[{i}]', limit)
            if len(out) >= limit:
                break
    elif got != ref and not (isinstance(got, float) and isinstance(ref, float)
                             and math.isnan(got) and math.isnan(ref)):
        out.append(f'{path}: пересчитано {str(got)[:80]}, сохранено {str(ref)[:80]}')
    return out[:limit]


def accept_rows(raw, expected, by_id, implementations, stand):
    """Итоговая строка на ключ — последняя с верным отпечатком; ранние допустимы, только если partial."""
    failures, final, superseded = [], {}, 0
    extra, bad_fp, repeated = [], [], []
    for row in raw:
        try:
            k = key(row)
        except (KeyError, TypeError, ValueError):
            failures.append('строка без ключа задания')
            continue
        if k not in expected:
            extra.append(k)
            continue
        if row.get('job_fingerprint') != stand.job_fingerprint(by_id[k[0]], k[1], k[2], implementations):
            bad_fp.append(k)
            continue
        if k in final:
            if final[k].get('run_status') == 'partial':
                superseded += 1
            else:
                repeated.append(k)
        final[k] = row
    if extra:
        failures.append(f'лишние задания: {len(extra)} (например {sorted(extra)[:3]})')
    if bad_fp:
        failures.append(f'отпечаток задания не совпал: {len(bad_fp)} (например {sorted(bad_fp)[:3]})')
    if repeated:
        failures.append(f'повтор поверх незавершённой не остановкой строки: {len(repeated)} (например {sorted(repeated)[:3]})')
    missing = expected - set(final)
    if missing:
        failures.append(f'не хватает заданий: {len(missing)} (например {sorted(missing)[:3]})')
    return failures, final, superseded


def check_unit_row(row, unit, stand, summary):
    tag = '/'.join(map(str, key(row)))
    failures = []
    for field, expected in (('host', unit['host']), ('measure', unit['measure']), ('unit', unit['unit']),
                            ('seed', unit['seed']), ('stand_version', stand.STAND_VERSION),
                            ('prototype_version', summary.get('prototype_version'))):
        if row.get(field) != expected:
            failures.append(f'{tag}: {field} = {row.get(field)!r}, ожидалось {expected!r}')
    if row.get('outcome') not in stand.CATEGORIES:
        failures.append(f'{tag}: неизвестный исход {row.get("outcome")!r}')
    wall = row.get('wall')
    if not isinstance(wall, (int, float)) or not math.isfinite(wall) or wall < 0:
        failures.append(f'{tag}: время задания {wall!r}')
    return failures


def verify(args):
    failures, checks = [], {}
    run = ROOT/args.run
    stand = stand_module()
    try:
        params = stand.normalize_params(args.per_cell, args.b_per_cell, args.unit_start, args.years, args.b_years)
    except ValueError as exc:
        return dict(run=args.run, passed=False, failures=[f'параметры задания: {exc}'], checks=checks)
    full_scope = stand.is_full_scope(params)
    unit_list = stand.units(params['per_cell'], params['unit_start'])
    by_id = {u['orbit_id']: u for u in unit_list}
    planned = stand.plan_keys(unit_list, params)
    expected = {k for keys in planned.values() for k in keys}
    try:
        summary = json.loads((run/'summary.json').read_text(encoding='utf-8'))
        stored = json.loads((run/'analysis.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return dict(run=args.run, passed=False, failures=[f'нет или повреждён summary/analysis: {exc}'], checks=checks)
    raw = read_jsonl(run/'jobs.jsonl', failures)
    implementations = summary.get('implementations') or {}
    if set(implementations) != {'compiled', 'B'}:
        failures.append('summary.implementations неполон')
        implementations = dict(compiled=None, B=None)
    found, final, superseded = accept_rows(raw, expected, by_id, implementations, stand)
    failures += found
    for row in final.values():
        failures += check_row(row)
        failures += check_unit_row(row, by_id[row['orbit_id']], stand, summary)
    checks['jobs'] = dict(rows=len(raw), accepted=len(final), expected=len(expected), superseded_partial=superseded)
    # Параметры и счётчики summary — против задания и пересчёта.
    for name in ('per_cell', 'b_per_cell', 'unit_start', 'years', 'b_years'):
        if (summary.get('params') or {}).get(name) != params[name]:
            failures.append(f"summary.params.{name} = {(summary.get('params') or {}).get(name)} не совпадает с заданием {params[name]}")
    rows = [final[k] for keys in planned.values() for k in keys if k in final]
    counts = dict(full_scope=full_scope, units=len(unit_list), units_sha256=stand.units_digest(unit_list),
                  planned=len(expected), done=len(rows), stage=stand.STAGE, plan=stand.PLAN,
                  stand_version=stand.STAND_VERSION,
                  stopped=sum(1 for r in rows if r['run_status'] == 'partial'),
                  failed=sum(1 for r in rows if r['run_status'] == 'failed'))
    for name, value in counts.items():
        if summary.get(name) != value:
            failures.append(f'summary.{name} = {summary.get(name)!r}, пересчитано {value!r}')
    analysis = roundtrip(stand.analyze(rows, unit_list, params, full_scope))
    diff = differences(analysis, stored)
    if diff:
        failures.append('analysis.json не совпадает с пересчётом: ' + '; '.join(diff))
    if summary.get('decision') != analysis['decision']:
        failures.append('summary.decision не совпадает с пересчётом: '
                        + '; '.join(differences(analysis['decision'], summary.get('decision') or {})))
    if summary.get('complete') != analysis['complete']:
        failures.append('summary.complete не совпадает с пересчётом')
    if args.expect_full and not full_scope:
        failures.append('ожидался полный объём P2-2a')
    if args.expect_complete and not (analysis['complete'] and counts['failed'] == 0):
        failures.append(f"ожидался завершённый run без отказов: завершён {analysis['complete']}, отказов {counts['failed']}")
    final_level = analysis['horizons'][f"{max(params['years']):g}"]
    checks['analysis'] = dict(complete=analysis['complete'], decision=analysis['decision']['verdict'],
                              stratum_verdict=analysis['decision']['stratum_verdict'],
                              discordant=analysis['decision']['discordant'], n=analysis['decision']['n'],
                              final_pairs=sorted(final_level['pairs']))
    return dict(run=args.run, passed=not failures, failures=failures, checks=checks)


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', required=True)
    parser.add_argument('--per-cell', type=int, default=128)
    parser.add_argument('--b-per-cell', type=int, default=16)
    parser.add_argument('--unit-start', type=int, default=0)
    parser.add_argument('--years', type=float, nargs='+', default=[1.0, 10.0, 100.0, 1000.0])
    parser.add_argument('--b-years', type=float, nargs='*', default=[100.0, 1000.0])
    parser.add_argument('--expect-full', action='store_true', help='требовать полный объём P2-2a')
    parser.add_argument('--expect-complete', action='store_true', help='требовать все задания завершёнными без отказов')
    parser.add_argument('--output', default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    report = verify(args)
    text = json.dumps(report, ensure_ascii=False, indent=1, default=float)
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    print(('ПРИНЯТ' if report['passed'] else 'НЕ ПРИНЯТ') + f": {args.run}; проверки {report['checks']}")
    for failure in report['failures']:
        print('  -', failure)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
