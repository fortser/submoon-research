"""Повторная оценка выгруженного P2-1 без интегрирования и изменения runs/."""
import hashlib
import importlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'pyproject.toml').is_file())
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'src'))
stand = importlib.import_module('admission_p2_horizons')

RUN = ROOT / 'runs/W2-P2-1-55404980'
OUT = ROOT / 'results/evidence/W2_P2-1_remote_55404980'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def key(row):
    return row['orbit_id'], row['years'], row['variant']


def finite(value):
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(finite(v) for v in value)
    return True


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    summary = load(RUN / 'summary.json')
    data = rows(RUN / 'jobs.jsonl')
    _, cases = stand.load_cases(ROOT)
    by_case = {c['orbit_id']: c for c in cases}
    assert set(by_case) == set(summary['cases']) and len(cases) == 48
    assert all(finite(r) for r in data), 'NaN/Inf'
    bad_fp = [key(r) for r in data if stand.job_fingerprint(
        by_case[r['orbit_id']], r['years'], r['variant'], summary['implementations']
    ) != r['job_fingerprint']]
    assert not bad_fp, bad_fp
    local_fp = stand.implementation_fingerprint()
    # Локальный Python 3.10.6 отличается от удалённого 3.10.12. Проверяем
    # fingerprint явно с версией среды запуска, не меняя производственный модуль.
    import submoon_research.dynamics.compiled_dop853 as compiled
    import numpy as np
    import numba
    import scipy
    h = hashlib.sha256()
    manifest = load(ROOT / 'scratch/p2_rental/bundle_manifest.json')['files']
    for name in compiled._FINGERPRINT_MODULES:
        module_path = sys.modules[name].__file__
        assert module_path is not None
        path = Path(module_path)
        assert digest(path) == manifest[path.relative_to(ROOT).as_posix()]
        h.update(name.encode())
        h.update(path.read_bytes())
    constants = dict(prototype_version=compiled.PROTOTYPE_VERSION,
                     safety=compiled.SAFETY, min_factor=compiled.MIN_FACTOR,
                     max_factor=compiled.MAX_FACTOR, error_exponent=compiled.ERROR_EXPONENT,
                     screen_margin=compiled.SCREEN_MARGIN, screen_depth=compiled.SCREEN_DEPTH,
                     eccentricity_limit=compiled.ECCENTRICITY_LIMIT,
                     python=summary['machine']['python'], numpy=np.__version__,
                     scipy=scipy.__version__, numba=numba.__version__)
    h.update(json.dumps(constants, sort_keys=True).encode())
    for name in ('_A', '_B', '_E3', '_E5', '_D', '_AX', '_NODES', '_INVERSE'):
        h.update(np.ascontiguousarray(getattr(compiled, name)).tobytes())
    assert h.hexdigest() == summary['implementations']['compiled']
    # B зависит от версий удалённого окружения: используем сохранённый fingerprint,
    # не подменяем его версиями локальной среды.
    evaluations = []
    for stored in summary['levels'] + summary['extension_levels']:
        subset = [r for r in data if r['years'] == stored['years'] and r['variant'] in stored['variants']]
        checked = stand.evaluate(subset, cases, stored['years'], stored['variants'],
                                 True, stored['kind'] == 'extension', stored['focus'])
        for field in ('passed', 'complete', 'Q1_failures', 'Q2_violations', 'problems'):
            assert checked[field] == stored[field], (stored['years'], field)
        assert checked['passed'] and checked['complete']
        checked['kind'] = stored['kind']
        checked['counted_in_original'] = stored['counted']
        evaluations.append(checked)
    old = {key(r): r for r in rows(ROOT / 'runs/W2-P2-1-55200007/jobs.jsonl')}
    repeat = [r for r in data if r['variant'] in ('D', 'T') and r['years'] <= 1000]
    exact_non_wall = []
    diagnostics = []
    for row in repeat:
        previous = old[key(row)]
        assert previous['job_fingerprint'] == row['job_fingerprint']
        comparison = stand.compare(row, previous, by_case[row['orbit_id']])
        exact = {k: v for k, v in row.items() if k != 'wall'} == {
            k: v for k, v in previous.items() if k != 'wall'}
        exact_non_wall.append(exact)
        diagnostics.append(dict(orbit_id=row['orbit_id'], years=row['years'],
                                variant=row['variant'], exact_except_wall=exact, **comparison))
    b1000 = [r for r in data if r['variant'] == 'B' and r['years'] == 1000]
    longest = max(b1000, key=lambda r: r['wall'])
    seed_keys = {key(r) for r in rows(ROOT / 'scratch/p2_rental/seed_jobs.jsonl')}
    new = [r for r in data if key(r) not in seed_keys]
    high_e = {v: sorted({r['orbit_id'] for r in data if r['variant'] == v and
                         (r.get('numerical_domain') or {}).get('outside_validated_domain')})
              for v in ('D', 'T')}
    report = dict(run_id='W2-P2-1-55404980', passed=True,
                  jobs_sha256=digest(RUN / 'jobs.jsonl'), jobs=len(data),
                  statuses=dict(Counter(r['run_status'] for r in data)),
                  outcomes_by_variant={v: dict(Counter(r['outcome'] for r in data if r['variant'] == v))
                                       for v in ('D', 'T', 'B')},
                  finite_numbers=True, fingerprint_checked_rows=len(data),
                  compiled_fingerprint_matches_local=(local_fp == summary['implementations']['compiled']),
                  compiled_fingerprint_reconstructed_for_remote_python=True,
                  fingerprint_environment=constants, evaluations=evaluations,
                  repeat_DT=dict(rows=len(repeat), exact_except_wall=sum(exact_non_wall),
                                 same_outcome=sum(d['same_outcome'] for d in diagnostics),
                                 Q2_passed=sum(d['Q2'] for d in diagnostics), details=diagnostics),
                  B1000=dict(completed=len(b1000),
                             max_endpoint_residual=max(r['endpoint_residual'] for r in b1000),
                             longest_orbit=longest['orbit_id'], longest_wall_seconds=longest['wall']),
                  high_e_orbits=high_e,
                  cost=dict(new_rows=len(new), new_job_wall_sum_hours=sum(r['wall'] for r in new)/3600,
                            stand_wall_hours=summary['wall_seconds']/3600,
                            note='Сумма wall заданий не является измеренным CPU-временем; seed не включён.'),
                  limitations=['Повторная оценка использует прежний оценщик; не независимая проверка физики.',
                               '48 разработочных случаев не являются вероятностной выборкой.',
                               'P2-2/P3/P4 и правило высоких e остаются открыты; production-допуска нет.'])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'data_audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('jobs', 'finite_numbers', 'fingerprint_checked_rows', 'B1000', 'cost')},
                     ensure_ascii=False, indent=2))
    print('D/T repeat:', len(repeat), 'exact except wall:', sum(exact_non_wall),
          'same outcome:', sum(d['same_outcome'] for d in diagnostics))
    print('All 7 evaluations reproduced; high-e:', {v: len(o) for v, o in high_e.items()})


if __name__ == '__main__':
    main()
