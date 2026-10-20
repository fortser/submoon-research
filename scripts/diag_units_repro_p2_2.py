"""W2-I035: где локальная генерация единиц P2-2a расходится с сервером (только диагностика).

Сравнивает в текущем окружении:
  1) units_sha256 полного P2-2a (128 ед./ячейку) с summary.json выгруженного run;
  2) стартовое состояние пробного тела каждой единицы с samples[0] задания D/1 год из jobs.jsonl
     (сервер записал его сам) — число несовпадений и максимум в ULP по мерам;
  3) для первой несовпавшей единицы (иначе — первой canonical_volume) промежуточные величины
     генератора (Sobol, нормировка quad, e через brentq, a, степени) в hex — против эталона
     --reference, если он задан (JSON того же формата из другого окружения);
  4) окружение: Python, glibc, numpy/scipy, BLAS, флаги CPU.
Ничего не меняет в run и в коде; пишет отчёт --output.

    python scripts/diag_units_repro_p2_2.py --run runs/W2-P2-2a-56220810 --output <json> [--reference <json>]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import platform
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
sys.path.insert(0, str(ROOT/'scripts'))


def hexf(x):
    return float(x).hex()


def ulps(a, b):
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    ia, ib = a.view(np.int64), b.view(np.int64)
    return int(np.max(np.abs(ia-ib))) if a.size else 0


def environment():
    import scipy
    flags = ''
    try:
        for line in open('/proc/cpuinfo', encoding='utf-8'):
            if line.startswith('flags'):
                flags = line.split(':', 1)[1]
                break
    except OSError:
        pass
    model = ''
    try:
        for line in open('/proc/cpuinfo', encoding='utf-8'):
            if line.startswith('model name'):
                model = line.split(':', 1)[1].strip()
                break
    except OSError:
        pass
    wanted = ('fma', 'avx2', 'avx512f', 'sse4_2')
    blas = None
    try:
        info = np.show_config(mode='dicts')
        blas = (info.get('Build Dependencies') or {}).get('blas')
    except Exception:  # noqa: BLE001
        pass
    return dict(python=platform.python_version(), implementation=platform.python_implementation(),
                platform=platform.platform(), libc=list(platform.libc_ver()), numpy=np.__version__,
                scipy=scipy.__version__, blas=blas, cpu=model,
                cpu_flags={f: f in flags.split() for f in wanted},
                float_repr_style=sys.float_repr_style, design_py=str(ROOT/'src/submoon_research/sampling/design.py'),
                pycache_prefix=sys.pycache_prefix, dont_write_bytecode=sys.dont_write_bytecode)


def stand_module():
    spec = importlib.util.spec_from_file_location('admission_p2_2', ROOT/'scripts'/'admission_p2_2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trace(stand, prepared, unit):
    """Промежуточные величины generate() для одной единицы — повтор кода design.generate шаг за шагом."""
    from scipy.integrate import quad
    from scipy.optimize import brentq
    from scipy.stats import qmc
    from submoon_research.sampling.design import MEASURES, domain_normalization
    setup = prepared[unit['host']]
    domain, measure = setup['domain'], unit['measure']
    normal, emax, width = domain_normalization(domain, measure)
    point = qmc.Sobol(d=6, scramble=True, seed=unit['seed']).random_base2(0)[0]
    target = point[0]*normal
    e = brentq(lambda x: quad(width, 0, x, epsabs=0, epsrel=1e-12)[0]-target, 0, emax, xtol=1e-14) if target else 0.0
    high, low = domain.hill_km, domain.inner_km
    lower = low/(1-e)
    out = dict(orbit_id=unit['orbit_id'], measure=measure, inner_km=hexf(low), hill_km=hexf(high),
               gm_host=hexf(domain.gm_host), emax=hexf(emax), normal=hexf(normal),
               sobol=[hexf(x) for x in point], target=hexf(target), e=hexf(e), lower=hexf(lower),
               quad_at_e=hexf(quad(width, 0, e, epsabs=0, epsrel=1e-12)[0]), width_at_e=hexf(width(e)))
    if measure == MEASURES[2]:
        out.update(high_pow_1_5=hexf(high**1.5), lower_pow_1_5=hexf(lower**1.5),
                   inner_sum=hexf(lower**1.5+(high**1.5-lower**1.5)*point[1]))
        out['a'] = hexf((lower**1.5+(high**1.5-lower**1.5)*point[1])**(2/3))
    elif measure == MEASURES[1]:
        out['a'] = hexf(lower*(high/lower)**point[1])
    else:
        out['a'] = hexf(lower+(high-lower)*point[1])
    out['a_unit'] = hexf(unit['a_km'])
    out['probe_state'] = [hexf(x) for x in unit['state'][-6:]]
    out['period'] = hexf(unit['period_seconds'])
    # Микропроверки степеней на тех же входах (libm pow).
    out['pow_checks'] = {'high**1.5': hexf(high**1.5), 'math.pow(high,1.5)': hexf(math.pow(high, 1.5)),
                         'np.power(high,1.5)': hexf(np.power(high, 1.5)),
                         'sqrt(high)**3': hexf(math.sqrt(high)**3)}
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=(__doc__ or '').splitlines()[0])
    parser.add_argument('--run', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--reference', default=None, help='JSON этого же скрипта из другого окружения')
    args = parser.parse_args(argv)
    run = ROOT/args.run
    summary = json.loads((run/'summary.json').read_text(encoding='utf-8'))
    server = {}
    with (run/'jobs.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['variant'] == 'D' and float(row['years']) == 1.0:
                server[row['orbit_id']] = row['samples'][0][1]
    stand = stand_module()
    prepared = stand.setups()
    units = stand.units(summary['params']['per_cell'], summary['params']['unit_start'], prepared)
    digest = stand.units_digest(units)
    by_measure, first_bad = {}, None
    for u in units:
        s = by_measure.setdefault(u['measure'], dict(units=0, probe_mismatch=0, max_ulp=0, missing=0))
        s['units'] += 1
        ref = server.get(u['orbit_id'])
        if ref is None:
            s['missing'] += 1
            continue
        d = ulps(u['state'][-6:], ref)
        if d:
            s['probe_mismatch'] += 1
            s['max_ulp'] = max(s['max_ulp'], d)
            first_bad = first_bad or u
    target = first_bad or next(u for u in units if u['measure'] == 'canonical_volume')
    report = dict(task='W2-I035', environment=environment(), run=args.run,
                  units_sha256=dict(local=digest, server=summary.get('units_sha256'),
                                    match=digest == summary.get('units_sha256')),
                  probe_vs_server=by_measure, trace=trace(stand, prepared, target),
                  massive_sha256={h: __import__('hashlib').sha256(np.asarray(p['massive']).tobytes()).hexdigest()
                                  for h, p in prepared.items()})
    if args.reference:
        ref = json.loads(Path(args.reference).read_text(encoding='utf-8'))
        rt, lt = ref.get('trace', {}), report['trace']
        report['reference_environment'] = ref.get('environment')
        report['trace_vs_reference'] = {k: dict(local=lt.get(k), reference=rt.get(k))
                                        for k in sorted(set(lt) | set(rt)) if lt.get(k) != rt.get(k)}
        report['massive_vs_reference'] = {h: v == (ref.get('massive_sha256') or {}).get(h)
                                          for h, v in report['massive_sha256'].items()}
    path = Path(args.output)
    path = path if path.is_absolute() else ROOT/path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    env = report['environment']
    print(f"окружение: Python {env['python']}, {env['libc']}, numpy {env['numpy']}, scipy {env['scipy']}, CPU {env['cpu']}")
    print(f"units_sha256: локально {digest[:16]}…, сервер {str(summary.get('units_sha256'))[:16]}…, "
          f"{'СОВПАДАЕТ' if report['units_sha256']['match'] else 'НЕ СОВПАДАЕТ'}")
    for m, s in by_measure.items():
        print(f'  {m}: единиц {s["units"]}, несовпадений старта {s["probe_mismatch"]}, макс. ULP {s["max_ulp"]}')
    print(f"трассировка: {report['trace']['orbit_id']}")
    if 'trace_vs_reference' in report:
        diff = report['trace_vs_reference']
        print('  отличия от эталона: ' + (', '.join(diff) if diff else 'нет'))
        for k, v in list(diff.items())[:12]:
            print(f"    {k}: локально {v['local']}, эталон {v['reference']}")
    print('отчёт:', path)
    return 0 if report['units_sha256']['match'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
