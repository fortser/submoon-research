"""W2-P2-2: планирование ансамблевой ступени — выборка генератором W1, модель стоимости, объём.

Не научный расчёт и не исполнение: ничего не интегрирует. Строит выборку P2-2 тем же генератором,
что W1 (sampling.design.generate), но каждая статистическая единица — независимая рандомизация из
одной точки (power=0, отдельный seed), поэтому исходы единиц независимы и допускают точные
биномиальные границы (единица — рандомизация, как в протоколе W1). Стоимость — по калибровке
P2-1 (runs/W2-P2-1-55404980, P-ядра i9-14900KF): шаг ограничен долей периода, поэтому стоимость
модельного года ~ (шагов на виток)/P · (мкс на шаг). Выживание неизвестно до расчёта и берётся
вилкой по геометрическому критерию H3: выживает, если a(1+e) <= c·RH0, c ∈ {0.3, 0.5, 0.7}.

    python scripts/plan_p2_2.py --per-cell 128 --output results/evidence/W2_P2-2_plan_20261012/plan.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

import numpy as np  # noqa: E402
from scipy.stats import beta  # noqa: E402

YEAR = 365.25*86400.0
HOSTS = ('iapetus', 'ganymede', 'himalia')
SEED_BASE = 205610051  # новый поток: W1 pilot=202…, main=203…, coverage=204…
# Калибровка P2-1 (P-ядро i9-14900KF, 8 воркеров): мкс на шаг по хозяину и шагов на виток.
US_PER_STEP = dict(D=dict(iapetus=12.2, ganymede=16.3, himalia=11.4),
                   T=dict(iapetus=12.1, ganymede=16.3, himalia=11.1),
                   B=dict(iapetus=320.0, ganymede=320.0, himalia=320.0))
STEPS_PER_ORBIT = dict(D=40.0, T=80.0, B=45.0)  # B: 36 до W2-I019, +11–18% с пределом шага
NON_SURVIVOR_YEARS = 1.0  # модельное время до ухода/контакта у невыживших (оценка сверху для outer)


def domains():
    import yaml
    from submoon_research.catalog.nominal_model import validate_model
    from submoon_research.contracts.nominal_states import validate_nominal_states
    from submoon_research.dynamics.initialization import PARENTS, physical_inputs
    from submoon_research.sampling.design import DomainSpec
    cfg = yaml.safe_load((ROOT/'configs/experiments/W2_three_engines_v1.yaml').read_text(encoding='utf-8'))
    model = validate_model(json.loads((ROOT/cfg['model_path']).read_text(encoding='utf-8')))
    states = validate_nominal_states(json.loads((ROOT/cfg['states_path']).read_text(encoding='utf-8')))
    out = {}
    for host in HOSTS:
        _, _, gms, _, radii, a_host, basis = physical_inputs(host, model, states)
        gm_parent = model['bodies'][PARENTS[host]]['gm']['value']
        out[host] = (DomainSpec(domain_id=host+'-nominal-v1', gm_host=gms[0], gm_parent=gm_parent,
                                a_host_km=a_host, reference_radius_km=radii[0]-model['submoon_radius_km'],
                                submoon_radius_km=model['submoon_radius_km'], eccentricity_max=0.3,
                                contact_buffer=0.02), basis)
    return out


def sample(per_cell):
    from submoon_research.sampling.design import MEASURES, generate
    doms = domains()
    cells = [(h, m) for h in HOSTS for m in MEASURES]
    points = []
    for j in range(per_cell):
        for c, (host, measure) in enumerate(cells):
            seed = SEED_BASE + len(cells)*j + c
            domain, basis = doms[host]
            rec = generate(domain, measure, power=0, seed=seed, randomization=j, basis=basis)['records'][0]
            el = rec['elements']
            points.append(dict(host=host, measure=measure, unit=j, seed=seed, a_km=el['a_km'], e=el['e'],
                               cos_i=el['cos_i'], a_over_rh=el['a_km']/domain.hill_km,
                               period_s=2*math.pi*math.sqrt(el['a_km']**3/domain.gm_host)))
    return points


def cost_seconds(point, variant, horizon_years, c):
    survives = point['a_over_rh']*(1+point['e']) <= c
    years = horizon_years if survives else min(horizon_years, NON_SURVIVOR_YEARS)
    orbits = years*YEAR/point['period_s']
    return orbits*STEPS_PER_ORBIT[variant]*US_PER_STEP[variant][point['host']]*1e-6, survives


def required_n(failures, alpha, margin=0.005):
    """Минимальное n: точная односторонняя (Клоппер–Пирсон) верхняя граница доли <= margin."""
    n = failures+1
    while beta.ppf(1-alpha, failures+1, n-failures) > margin:
        n += 1 if n < 200 else max(1, n//200)
    return n


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--per-cell', type=int, default=128)
    parser.add_argument('--b-per-cell', type=int, default=16)
    parser.add_argument('--horizons', type=float, nargs='+', default=[100.0, 1000.0, 10000.0])
    parser.add_argument('--output', default=None)
    args = parser.parse_args()
    points = sample(args.per_cell)
    report = dict(per_cell=args.per_cell, cells=9, units=len(points), seed_base=SEED_BASE,
                  calibration=dict(us_per_step=US_PER_STEP, steps_per_orbit=STEPS_PER_ORBIT,
                                   source='runs/W2-P2-1-55404980, выжившие 1000 лет, P-ядра i9-14900KF'),
                  sample=dict(a_over_rh_quantiles=np.quantile([p['a_over_rh'] for p in points], [0.1, 0.25, 0.5, 0.75, 0.9]).tolist(),
                              period_days_quantiles=np.quantile([p['period_s']/86400 for p in points], [0.01, 0.1, 0.5, 0.9]).tolist()),
                  costs=[], sample_size=[])
    print(f'единиц {len(points)} (9 ячеек × {args.per_cell}); a/RH0 квантили 10/25/50/75/90%: '
          + ', '.join(f'{x:.3f}' for x in report['sample']['a_over_rh_quantiles']))
    print("период, сут, квантили 1/10/50/90%: " + ', '.join(f'{x:.2f}' for x in report['sample']['period_days_quantiles']))
    print(f"\n{'H, лет':>7} {'c':>4} {'выжив.':>7} {'D, ядро-ч':>10} {'T, ядро-ч':>10} {'B подвыб., ядро-ч':>18} {'макс. T, ч':>11} {'макс. B, ч':>11}")
    b_points = [p for p in points if p['unit'] < args.b_per_cell]
    for horizon in args.horizons:
        for c in (0.3, 0.5, 0.7):
            d = [cost_seconds(p, 'D', horizon, c) for p in points]
            t = [cost_seconds(p, 'T', horizon, c) for p in points]
            b = [cost_seconds(p, 'B', horizon, c) for p in b_points]
            row = dict(horizon_years=horizon, c=c, survivors=sum(s for _, s in d)/len(d),
                       D_core_hours=sum(x for x, _ in d)/3600, T_core_hours=sum(x for x, _ in t)/3600,
                       B_core_hours=sum(x for x, _ in b)/3600, B_units=len(b_points),
                       max_T_hours=max(x for x, _ in t)/3600, max_B_hours=max(x for x, _ in b)/3600)
            report['costs'].append(row)
            print(f"{horizon:7g} {c:4.1f} {row['survivors']:7.2f} {row['D_core_hours']:10.1f} {row['T_core_hours']:10.1f} "
                  f"{row['B_core_hours']:18.1f} {row['max_T_hours']:11.2f} {row['max_B_hours']:11.2f}")
    print('\nМинимальное число независимых единиц: точная односторонняя 95%-граница доли расхождений <= 0.005')
    print(f"{'расхождений':>11} {'1 страта':>9} {'3 страты (хозяева)':>19} {'9 страт (ячейки)':>17}")
    for failures in (0, 1, 2, 3, 5, 10):
        n = [required_n(failures, 0.05/s) for s in (1, 3, 9)]
        report['sample_size'].append(dict(failures=failures, one=n[0], hosts=n[1], cells=n[2]))
        print(f'{failures:11d} {n[0]:9d} {n[1]:19d} {n[2]:17d}')
    if args.output:
        path = ROOT/args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(report, points=points), ensure_ascii=False, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
