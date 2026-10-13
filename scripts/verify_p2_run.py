"""Строгая приёмка выгруженного run стенда P2 (W2-I020): fail-closed, состав — из параметров задания.

Прежний аудитор брал перечень ступеней из проверяемого summary.json и принимал копию без целой
ступени. Здесь ожидаемый состав строится из параметров утверждённой команды стенда (--years,
--b-max-years, --core-years, --core-b-max) и закреплённого набора случаев load_cases; ступени
заново оцениваются критериями стенда и сверяются с level_*.json и summary.json. Любое
расхождение — провал (код выхода 1); отчёт перечисляет все найденные нарушения.

    python scripts/verify_p2_run.py --run runs/W2-P2-1-55404980 --years 1 10 100 1000 10000 \\
        --b-max-years 1000 --seed-info results/evidence/W2_P2-1_remote_55404980/seed_info.json \\
        --repeat-of runs/W2-P2-1-55200007 --repeat-years 1 10 100 1000 --expect-passed
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
YEAR = 365.25*86400.0
EVENT_OUTCOME = {'reference_contact': 'host_contact', 'other_contact': 'other_contact',
                 'operational_escape': 'operational_escape'}
UNRESOLVED_REASONS = {'computational', 'high_e_prolonged', 'high_e_open_at_horizon',
                      'escape_not_resolved_at_horizon'}


def stand_module():
    spec = importlib.util.spec_from_file_location('admission_p2_horizons', ROOT/'scripts'/'admission_p2_horizons.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def key(row):
    return (row['orbit_id'], float(row['years']), row['variant'])


def finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(v) for v in value)
    return True


def read_jsonl(path, failures):
    rows = []
    for number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            failures.append(f'{path}: строка {number} не разбирается')
    return rows


def expected_keys(case_ids, years, b_max):
    return {(c, float(y), v) for c in case_ids for y in years for v in ('D', 'T', 'B')
            if v != 'B' or y <= b_max}


def check_composition(rows, expected):
    failures = []
    keys = [key(r) for r in rows]
    if len(keys) != len(set(keys)):
        failures.append(f'повторяющиеся задания: {len(keys) - len(set(keys))}')
    missing, extra = expected - set(keys), set(keys) - expected
    if missing:
        failures.append(f'не хватает заданий: {len(missing)} (например {sorted(missing)[:3]})')
    if extra:
        failures.append(f'лишние задания: {len(extra)} (например {sorted(extra)[:3]})')
    return failures


def check_row(row):
    """Внутренняя согласованность одной строки (статус, время, сэмплы, событие, исход)."""
    failures = []
    tag = '/'.join(map(str, key(row)))
    if not finite(row):
        failures.append(f'{tag}: NaN/Inf')
    status = row.get('run_status')
    # Отказ задания — законный результат (его оценивает P2-Q1 при пересчёте ступеней); здесь —
    # только согласованность записи: у completed нет ошибки, у failed/partial она есть.
    if status not in ('completed', 'failed', 'partial'):
        failures.append(f'{tag}: неизвестный статус {status}')
    elif status == 'completed' and (row.get('error') is not None or row.get('stopped')):
        failures.append(f'{tag}: completed с ошибкой или остановкой')
    elif status != 'completed' and (not row.get('error') or row.get('outcome') != 'unresolved'):
        failures.append(f'{tag}: {status} без текста ошибки или с исходом {row.get("outcome")}')
    horizon = float(row['years'])*YEAR
    end = row['last_valid_time']
    if not 0 <= end <= horizon:
        failures.append(f'{tag}: last_valid_time вне горизонта')
    samples = row.get('samples') or []
    times = [t for t, _ in samples]
    if not samples or times[0] != 0:
        failures.append(f'{tag}: нет начальной точки')
    if any(len(s) != 6 for _, s in samples) or len(row.get('final_probe') or []) != 6:
        failures.append(f'{tag}: форма состояния')
    if any(a >= b for a, b in zip(times, times[1:])) or any(t > end for t in times):
        failures.append(f'{tag}: сэмплы не монотонны или позже конца')
    event, outcome = row.get('event'), row.get('outcome')
    reason = row.get('unresolved_reason')
    domain = row.get('numerical_domain') or {}
    if status != 'completed':
        pass  # незавершённое задание: конец — last_valid_time до горизонта, события нет
    elif event:
        if event['time'] != end:
            failures.append(f'{tag}: событие не совпадает с концом')
        if EVENT_OUTCOME.get(event['event']) != outcome:
            failures.append(f'{tag}: событие/исход не согласованы')
    elif reason == 'high_e_prolonged':
        if outcome != 'unresolved' or domain.get('high_e_phase') != 'prolonged' or end != domain.get('high_e_stop_time'):
            failures.append(f'{tag}: останов высоких e не согласован')
    elif end != horizon:
        failures.append(f'{tag}: завершено без события до горизонта')
    elif outcome not in ('survived', 'unresolved'):
        failures.append(f'{tag}: неожиданный исход {outcome}')
    if 'unresolved_reason' in row or int(row.get('prototype_version') or 0) >= 5:
        if (outcome == 'unresolved') != (reason is not None) or (reason and reason not in UNRESOLVED_REASONS):
            failures.append(f'{tag}: причина unresolved {reason!r} при исходе {outcome}')
    return failures


def verify(args):
    failures, checks = [], {}
    run = ROOT/args.run
    stand = stand_module()
    _, cases = stand.load_cases(ROOT)
    if args.limit is None and len(cases) != stand.FULL_CASE_COUNT:
        failures.append(f'набор случаев {len(cases)} вместо {stand.FULL_CASE_COUNT}')
    case_ids = [c['orbit_id'] for c in cases]
    rows = read_jsonl(run/'jobs.jsonl', failures)
    expected = expected_keys(case_ids, args.years, args.b_max_years)
    failures += check_composition(rows, expected)
    for row in rows:
        failures += check_row(row)
    checks['jobs'] = dict(rows=len(rows), expected=len(expected))
    summary = json.loads((run/'summary.json').read_text(encoding='utf-8'))
    for name, value in (('years', args.years), ('b_max_years', args.b_max_years),
                        ('core_years', args.core_years), ('core_b_max', args.core_b_max)):
        stored = summary.get(name)
        if (sorted(map(float, stored)) if isinstance(value, list) else float(stored)) != \
                (sorted(map(float, value)) if isinstance(value, list) else float(value)):
            failures.append(f'summary.{name} = {stored} не совпадает с заданием {value}')
    if summary.get('cases') != case_ids:
        failures.append('summary.cases не совпадает с закреплённым набором случаев')
    implementations = summary.get('implementations') or {}
    by_case = {c['orbit_id']: c for c in cases}
    bad_fp = [key(r) for r in rows if r['orbit_id'] in by_case and stand.job_fingerprint(
        by_case[r['orbit_id']], r['years'], r['variant'], implementations) != r.get('job_fingerprint')]
    if bad_fp:
        failures.append(f'отпечаток задания не совпал: {len(bad_fp)} (например {bad_fp[:3]})')
    # Заново: ступени по плану из параметров задания, затем сверка с level_*.json и summary.
    plan = stand.level_plan(args.years, args.b_max_years, args.core_years, args.core_b_max)
    by_key = {key(r): r for r in rows}
    full_scope = (args.limit is None and len(cases) == stand.FULL_CASE_COUNT
                  and tuple(args.core_years) == stand.FULL_YEARS and all(y in args.years for y in stand.FULL_YEARS)
                  and args.core_b_max >= stand.FULL_B_MAX and args.b_max_years >= stand.FULL_B_MAX)
    recomputed = {'core': [], 'extension': []}
    core_stop = ext_stop = None
    for years in args.years:
        level = plan[years]
        level_rows = [by_key[(c, float(years), v)] for c in case_ids for v in level['variants']
                      if (c, float(years), v) in by_key]
        try:
            stored = json.loads((run/f'level_{years:g}y.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            failures.append(f'нет или повреждён level_{years:g}y.json')
            stored = {}
        for kind in ('core', 'extension'):
            variants = level['core'] if kind == 'core' else level['variants']
            if not (level['core'] if kind == 'core' else level['extension']):
                if stored.get(kind):
                    failures.append(f'{years:g} лет: лишняя оценка {kind}')
                continue
            subset = [r for r in level_rows if r['variant'] in variants]
            got = stand.evaluate(subset, cases, years, variants, full_scope, allow_stopped=kind == 'extension',
                                 focus=None if kind == 'core' else level['extension'])
            got['counted'] = core_stop is None and (kind == 'core' or ext_stop is None)
            recomputed[kind].append(got)
            ref = stored.get(kind)
            if not ref:
                failures.append(f'{years:g} лет: нет сохранённой оценки {kind}')
            else:
                for field in ('passed', 'complete', 'counted', 'problems'):
                    if got[field] != ref.get(field):
                        failures.append(f'{years:g} лет {kind}: {field} пересчитан {got[field]}, сохранён {ref.get(field)}')
                for field in ('Q1_failures', 'Q2_violations'):
                    if len(got[field]) != len(ref.get(field) or []):
                        failures.append(f'{years:g} лет {kind}: {field} пересчитано {len(got[field])}, '
                                        f'сохранено {len(ref.get(field) or [])}')
            if stand.blocking(got):
                if kind == 'core' and core_stop is None:
                    core_stop = years
                if kind == 'extension' and core_stop is None and ext_stop is None:
                    ext_stop = years
    for kind, name in (('core', 'levels'), ('extension', 'extension_levels')):
        stored_levels = summary.get(name) or []
        if [float(s['years']) for s in stored_levels] != [float(s['years']) for s in recomputed[kind]]:
            failures.append(f'summary.{name}: ступени {[s["years"] for s in stored_levels]}, '
                            f'по заданию {[s["years"] for s in recomputed[kind]]}')
    verdict = (full_scope and core_stop is None
               and all(s['passed'] and s['counted'] for s in recomputed['core'])
               and {float(s['years']) for s in recomputed['core']} >= set(stand.FULL_YEARS))
    extension_passed = bool(recomputed['extension']) and all(
        s['passed'] and s['counted'] and s['complete'] for s in recomputed['extension'])
    if summary.get('verdict_passed') != verdict or summary.get('extension_passed') != extension_passed:
        failures.append(f'итог summary ({summary.get("verdict_passed")}, {summary.get("extension_passed")}) '
                        f'не совпадает с пересчётом ({verdict}, {extension_passed})')
    if args.expect_passed and not (verdict and (extension_passed or not recomputed['extension'])):
        failures.append(f'ожидался пройденный run: обязательный {verdict}, расширение {extension_passed}')
    checks['levels'] = dict(core=[s['years'] for s in recomputed['core']],
                            extension=[s['years'] for s in recomputed['extension']],
                            verdict_passed=verdict, extension_passed=extension_passed,
                            pairs=sum(len(s['pairs']) for k in recomputed for s in recomputed[k]))
    if args.seed_info:
        info = json.loads((ROOT/args.seed_info).read_text(encoding='utf-8'))
        source = ROOT/info['source_jobs']
        if hashlib.sha256(source.read_bytes()).hexdigest() != info['source_sha256']:
            failures.append('SHA источника seed не совпал')
        old = {key(r): r for r in read_jsonl(source, failures)}
        seed_keys = {k for k in expected if k[2] in info['variants'] and k[1] in {float(y) for y in info['years']}}
        if len(seed_keys) != int(info['rows']):
            failures.append(f'seed: по заданию {len(seed_keys)} строк, в seed_info {info["rows"]}')
        differ = [k for k in seed_keys if by_key.get(k) != old.get(k)]
        if differ:
            failures.append(f'seed: {len(differ)} строк отличаются от источника (например {differ[:3]})')
        checks['seed'] = dict(rows=len(seed_keys), differ=len(differ))
    if args.repeat_of:
        old = {key(r): r for r in read_jsonl(ROOT/args.repeat_of/'jobs.jsonl', failures)}
        repeat_keys = {k for k in expected if k[2] in args.repeat_variants and k[1] in {float(y) for y in args.repeat_years}}
        strip = lambda r: {k: v for k, v in (r or {}).items() if k != 'wall'}  # noqa: E731
        differ = [k for k in repeat_keys if k not in old or strip(by_key.get(k)) != strip(old[k])]
        if differ:
            failures.append(f'повтор: {len(differ)} из {len(repeat_keys)} строк не совпадают с {args.repeat_of} '
                            f'(например {sorted(differ)[:3]})')
        checks['repeat'] = dict(rows=len(repeat_keys), differ=len(differ))
    return dict(run=args.run, passed=not failures, failures=failures, checks=checks)


def parse(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', required=True)
    parser.add_argument('--years', type=float, nargs='+', required=True)
    parser.add_argument('--b-max-years', type=float, required=True)
    parser.add_argument('--core-years', type=float, nargs='*', default=[1.0, 10.0, 100.0, 1000.0])
    parser.add_argument('--core-b-max', type=float, default=10.0)
    parser.add_argument('--limit', type=int, default=None)
    parser.add_argument('--seed-info', default=None)
    parser.add_argument('--repeat-of', default=None)
    parser.add_argument('--repeat-variants', nargs='*', default=['D', 'T'])
    parser.add_argument('--repeat-years', type=float, nargs='*', default=[1.0, 10.0, 100.0, 1000.0])
    parser.add_argument('--expect-passed', action='store_true')
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
