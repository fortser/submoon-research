"""W2-T049: the B version with checkpoint implementation id (W2-I039) must not change numerics.

Runs the previous engine_compare (B fingerprint 0a1e70cf, frozen copy in scratch/claude) and the
current one on identical inputs and requires bitwise-equal outputs except the checkpoint contract
hash and timing fields. Cases: one synthetic 3-body system and three fast archived P2-2a units
(legacy generator, accepted only on exact archived job fingerprint and initial probe).
"""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))

import numpy as np  # noqa: E402

import admission_p2_2 as stand  # noqa: E402
from smoke_b_cm_start import JOBS_SHA, RUN, SUMMARY_SHA  # noqa: E402
from submoon_research.contracts.p2_files import atomic_json, canonical, file_digest, loads, read_json  # noqa: E402
from submoon_research.dynamics import engine_compare as new  # noqa: E402
from submoon_research.exact_linalg import LEGACY, numerics  # noqa: E402

OLD_PATH = ROOT/'scratch/claude/engine_compare.W2-T043_0a1e70cf.py'
OLD_SHA = '740a933809f703cd'
OUT = ROOT/'results/evidence/W2_B_version_20261021/numerics_unchanged.json'
CASES = (('ganymede:uniform_log_a:0002', 100.0), ('himalia:canonical_volume:0010', 100.0),
         ('himalia:uniform_a_e_cos_i_phases:0015', 10.0))
TIMING = {'wall_seconds', 'cpu_seconds', 'cache_preparation_wall_seconds', 'cache_preparation_cpu_seconds'}


def load_old():
    if not file_digest(OLD_PATH).startswith(OLD_SHA):
        raise ValueError('Frozen previous engine_compare changed')
    spec = importlib.util.spec_from_file_location('engine_compare_0a1e70cf', OLD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def comparable(out):
    out = {k: v for k, v in out.items() if k not in TIMING}
    out['checkpoint'] = {k: v for k, v in out['checkpoint'].items() if k != 'contract_sha256'}
    return out


def same(a, b):
    a, b = comparable(a), comparable(b)
    keys = sorted(set(a) | set(b))
    diff = [k for k in keys if canonical(a.get(k)) != canonical(b.get(k))]
    return diff


def main():
    if OUT.exists():
        raise SystemExit('numerics_unchanged.json already exists; no reruns')
    old = load_old()
    results = {}
    gms = [1., 1000.]
    initial = np.array([[0., 0., 0., 0., 0., 0.], [100., 0., 0., 0., math.sqrt(10.01), 0.],
                        [1., 0., 0., 0., 1., 0.02]])
    args = ('ias15_reboundx', initial, gms, [], [0.01, 1.], 60.)
    kw = dict(period=2*math.pi, times=np.linspace(0., 60., 61))
    a, b = old.integrate_engine(*args, **kw), new.integrate_engine(*args, **kw)
    results['synthetic'] = dict(differing_fields=same(a, b), steps=b['steps'], outcome=b['physical_outcome'],
                                contract_old=a['checkpoint']['contract_sha256'],
                                contract_new=b['checkpoint']['contract_sha256'])
    if file_digest(RUN/'summary.json') != SUMMARY_SHA or file_digest(RUN/'jobs.jsonl') != JOBS_SHA:
        raise ValueError('Pinned P2-2a source changed')
    summary = read_json(RUN/'summary.json')
    rows = {}
    for line in (RUN/'jobs.jsonl').read_text(encoding='utf-8').splitlines():
        r = loads(line)
        rows[(r['orbit_id'], r['years'], r['variant'])] = r
    with numerics(summary.get('generator_numerics', LEGACY)):
        prepared = stand.setups()
        units = {o: next(u for u in stand.units(1, int(o.rsplit(':', 1)[1]), prepared) if u['orbit_id'] == o)
                 for o, _ in CASES}
    for orbit, years in CASES:
        unit = units[orbit]
        ref_years = 100.0  # archived B rows exist for 100/1000 years; identity check on the 100-year row
        archived = rows[(orbit, ref_years, 'B')]
        ok = (stand.job_fingerprint(unit, ref_years, 'B', summary['implementations']) == archived['job_fingerprint']
              and np.array_equal(unit['state'][-6:], archived['samples'][0][1]))
        if not ok:
            results[orbit] = dict(skipped='identity mismatch')
            continue
        horizon = years*stand.YEAR
        call = ('ias15_reboundx', unit['state'], unit['gms'], unit['figures'], unit['radii'], horizon)
        kw = dict(period=unit['period_seconds'], mode=new.NumericMode(), hill_km=unit['hill_km'],
                  escape_window=unit['escape_window'], times=stand.grid(horizon))
        a, b = old.integrate_engine(*call, **kw), new.integrate_engine(*call, **kw)
        results[orbit] = dict(years=years, differing_fields=same(a, b), steps=b['steps'],
                              outcome=b['physical_outcome'], status=b['run_status'])
        print(orbit, years, results[orbit], flush=True)
    passed = all(not r.get('skipped') and r['differing_fields'] == [] for r in results.values())
    out = dict(task='W2-T049', rule=__doc__, old_engine_sha256=file_digest(OLD_PATH),
               new_engine_sha256=file_digest(ROOT/'src/submoon_research/dynamics/engine_compare.py'),
               b_implementation_new=new.b_implementation_fingerprint(), results=results, passed=passed)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(OUT, out)
    print('synthetic', results['synthetic'])
    print('NUMERICS UNCHANGED passed =', passed)
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
