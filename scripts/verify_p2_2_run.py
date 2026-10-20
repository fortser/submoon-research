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

--float-rtol (W2-D036) — допуск для производных float при пересчёте в другом окружении (версии
Python/scipy дают расхождения в последних битах сумм и квантилей); по умолчанию 0.

--expect-decision-ready (W2-D026) — вместо --expect-complete, когда задания B на финальном
горизонте остановлены сроком: D и T должны быть завершены без отказов на всех горизонтах.
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
from submoon_research.contracts.p2_files import (  # noqa: E402
    atomic_json, contract_digest, file_digest, output_path, read_json, validate_units_document,
)
from submoon_research.contracts.frozen_p2 import load_frozen_inputs  # noqa: E402


def stand_module():
    spec = importlib.util.spec_from_file_location('admission_p2_2', ROOT/'scripts'/'admission_p2_2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key(row):
    return (row['orbit_id'], float(row['years']), row['variant'])


def roundtrip(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=float))


def floats_close(got, ref, rtol):
    """W2-D036: производные float сравниваются точно (rtol=0) или с относительным допуском."""
    if not (isinstance(got, float) and isinstance(ref, float)):
        return False
    if math.isnan(got) and math.isnan(ref):
        return True
    return rtol > 0 and math.isfinite(got) and math.isfinite(ref) and abs(got-ref) <= rtol*max(abs(got), abs(ref))


def differences(got, ref, path='', limit=10, rtol=0.0):
    """Пути, где пересчитанное расходится с сохранённым (первые limit). Целые, строки, булевы,
    структура и состав сравниваются всегда точно; float — точно или с допуском rtol (W2-D036)."""
    out = []
    if isinstance(got, dict) and isinstance(ref, dict):
        for k in sorted(set(got) | set(ref), key=str):
            if k not in got or k not in ref:
                out.append(f'{path}/{k}: есть только в {"пересчёте" if k in got else "сохранённом"}')
            else:
                out += differences(got[k], ref[k], f'{path}/{k}', limit, rtol)
            if len(out) >= limit:
                break
    elif isinstance(got, list) and isinstance(ref, list) and len(got) == len(ref):
        for i, (a, b) in enumerate(zip(got, ref)):
            out += differences(a, b, f'{path}[{i}]', limit, rtol)
            if len(out) >= limit:
                break
    elif got != ref and not floats_close(got, ref, rtol):
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


def numerics_of(run):
    """Режим генератора для проверки run: 'exact-fsum-v1' у новых, 'legacy-blas' у run до W2-I035."""
    from submoon_research.exact_linalg import LEGACY
    try:
        summary = json.loads((ROOT/run/'summary.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return summary.get('generator_numerics', LEGACY)


def verify(args):
    """Проверка в той численной схеме генератора, которой создан run (W2-D036)."""
    from submoon_research.exact_linalg import LEGACY, NUMERICS_VERSION, numerics
    name = numerics_of(args.run)
    if name not in (None, LEGACY, NUMERICS_VERSION):
        return dict(run=args.run, passed=False, checks=dict(generator_numerics=name),
                    failures=[f'summary.generator_numerics = {name!r}: неизвестная численная схема генератора'])
    with numerics(name or NUMERICS_VERSION):
        report = _verify(args, name)
    report['checks']['generator_numerics'] = name
    return report


def _verify(args, numerics_name):
    failures, checks = [], {}
    rtol = float(args.float_rtol)
    if not 0 <= rtol <= 1e-9:
        return dict(run=args.run, passed=False, failures=['--float-rtol вне [0, 1e-9]'], checks=checks)
    checks['float_rtol'] = rtol
    run = ROOT/args.run
    stand = stand_module()
    try:
        params = stand.normalize_params(args.per_cell, args.b_per_cell, args.unit_start, args.years, args.b_years)
    except ValueError as exc:
        return dict(run=args.run, passed=False, failures=[f'параметры задания: {exc}'], checks=checks)
    full_scope = stand.is_full_scope(params)
    try:
        summary = read_json(run/'summary.json')
        stored = read_json(run/'analysis.json')
        if not isinstance(summary, dict) or not isinstance(stored, dict):
            raise ValueError('ожидались JSON-объекты')
    except (OSError, ValueError) as exc:
        return dict(run=args.run, passed=False, failures=[f'нет или повреждён summary/analysis: {exc}'], checks=checks)
    try:
        if bool(args.inputs_manifest) != bool(args.inputs_manifest_sha256):
            raise ValueError('Нужны вместе manifest и внешний SHA-256')
        if args.inputs_manifest:
            expected_doc, _ = load_frozen_inputs(ROOT/args.inputs_manifest, args.inputs_manifest_sha256,
                params=params, numerical_contract=stand.units_document([], params, summary.get('implementations'))['contract'])
            unit_list = expected_doc['units']
            if expected_doc['generator_numerics'] != numerics_name:
                raise ValueError('Происхождение frozen inputs отличается от summary')
            checks['input_basis'] = 'frozen; generator not called'
            checks['inputs_manifest_sha256'] = args.inputs_manifest_sha256
        else:
            unit_list = stand.units(params['per_cell'], params['unit_start'])
            expected_doc = stand.units_document(unit_list, params, summary.get('implementations'))
            expected_doc['generator_numerics'] = numerics_name
            expected_doc['contract_sha256'] = contract_digest(expected_doc)
            checks['input_basis'] = 'generator regression (not frozen-input acceptance)'
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(run=args.run, passed=False, failures=[f'frozen inputs: {exc}'], checks=checks)
    by_id = {u['orbit_id']: u for u in unit_list}
    planned = stand.plan_keys(unit_list, params)
    expected = {k for keys in planned.values() for k in keys}
    units_file = run/'units.json'
    if units_file.exists():
        try:
            doc = read_json(units_file)
            validate_units_document(doc, expected_doc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(f'units.json повреждён: {exc}')
            checks['units_file'] = 'invalid'
        else:
            raw_sha = file_digest(units_file)
            if doc.get('units_sha256') != summary.get('units_sha256'):
                failures.append('units.json: стартовые векторы не совпадают с summary.units_sha256')
            if summary.get('units_file_sha256') not in (None, raw_sha):
                failures.append('units.json: SHA-256 файла не совпадает с summary.units_file_sha256')
            if summary.get('input_contract_sha256') != doc.get('contract_sha256'):
                failures.append('units.json: полный SHA контракта не совпадает с summary')
            checks['units_file'] = 'match' if not any(f.startswith('units.json') for f in failures) else 'mismatch'
    else:
        checks['units_file'] = 'absent'
        if summary.get('units_file_sha256') or summary.get('input_contract_sha256'):
            failures.append('units.json: отсутствует заявленный файл входов')
    if summary.get('inputs_manifest_sha256'):
        try:
            local_doc, _ = load_frozen_inputs(run/'inputs_manifest.json', summary['inputs_manifest_sha256'], params=params)
            validate_units_document(local_doc, expected_doc)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            failures.append(f'inputs manifest: {exc}')
    raw = read_jsonl(run/'jobs.jsonl', failures)
    implementations = summary.get('implementations') or {}
    if set(implementations) != {'compiled', 'B'}:
        failures.append('summary.implementations неполон')
        implementations = dict(compiled=None, B=None)
    found, final, superseded = accept_rows(raw, expected, by_id, implementations, stand)
    failures += found
    if found and numerics_name == 'legacy-blas':
        failures.append('run создан до W2-I035 и проверен в режиме legacy-blas (прежние операции BLAS): '
                        'несовпадение стартов означает другое ядро OpenBLAS, чем при расчёте; проверять на '
                        'процессоре с тем же ядром (P2-2a 56220810 — AVX-512, OPENBLAS_CORETYPE=SkylakeX), '
                        'см. W2-D036 и docs/compute/W2_P2-2a_56220810_acceptance.md')
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
    diff = differences(analysis, stored, rtol=rtol)
    if diff:
        failures.append('analysis.json не совпадает с пересчётом: ' + '; '.join(diff))
    decision_diff = differences(analysis['decision'], summary.get('decision') or {}, rtol=rtol)
    if decision_diff:
        failures.append('summary.decision не совпадает с пересчётом: ' + '; '.join(decision_diff))
    if summary.get('complete') != analysis['complete']:
        failures.append('summary.complete не совпадает с пересчётом')
    if args.expect_full and not full_scope:
        failures.append('ожидался полный объём P2-2a')
    if args.expect_complete and not (analysis['complete'] and counts['failed'] == 0):
        failures.append(f"ожидался завершённый run без отказов: завершён {analysis['complete']}, отказов {counts['failed']}")
    # W2-D026: D/T завершены на всех горизонтах без отказов; B может быть остановлено сроком.
    dt_complete = all(level['dt_complete'] for level in analysis['horizons'].values())
    dt_failed = sum(1 for r in rows if r['run_status'] == 'failed' and r['variant'] in ('D', 'T'))
    b_unfinished = sum(level['b_unfinished'] for level in analysis['horizons'].values())
    if args.expect_decision_ready and not (dt_complete and dt_failed == 0):
        failures.append(f'ожидались завершённые D/T без отказов (W2-D026): D/T завершены {dt_complete}, '
                        f'отказов D/T {dt_failed}')
    final_level = analysis['horizons'][f"{max(params['years']):g}"]
    checks['analysis'] = dict(complete=analysis['complete'], dt_complete=dt_complete, dt_failed=dt_failed,
                              b_unfinished=b_unfinished, decision=analysis['decision']['verdict'],
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
    parser.add_argument('--expect-decision-ready', action='store_true',
                        help='W2-D026: требовать завершённые D/T без отказов на всех горизонтах; B может быть остановлено')
    parser.add_argument('--float-rtol', type=float, default=0.0,
                        help='W2-D036: относительный допуск для производных float в analysis/summary.decision '
                             '(по умолчанию 0 — точно; не больше 1e-9). Отпечатки, состав и счётчики — всегда точно')
    parser.add_argument('--inputs-manifest', help='проверка по frozen inputs без регенерации')
    parser.add_argument('--inputs-manifest-sha256', help='независимо закреплённый SHA манифеста')
    parser.add_argument('--output', default=None)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse(argv)
    try:
        path = output_path(ROOT/args.output, ROOT/args.run) if args.output else None
        if path is not None and args.inputs_manifest:
            output_path(path, (ROOT/args.inputs_manifest).resolve().parent)
    except ValueError as exc:
        print(f'ОТКАЗ (ничего не записано): {exc}')
        return 2
    report = verify(args)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, report)
    print(('ПРИНЯТ' if report['passed'] else 'НЕ ПРИНЯТ') + f": {args.run}; проверки {report['checks']}")
    for failure in report['failures']:
        print('  -', failure)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
