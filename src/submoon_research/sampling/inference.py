"""Одновременные границы и решения W1 с сохранением нерешённых исходов."""
import itertools
import math

import numpy as np
from scipy.integrate import quad

from submoon_research.contracts.w1 import EnsembleRow
from submoon_research.sampling.design import domain_normalization, MEASURES


def bounded_interval(lower, upper=None, *, bounds=(0.0, 1.0), alpha=0.05, comparisons=1):
    """Hoeffding по независимым scrambles; upper включает неизвестные исходы."""
    lo = np.asarray(lower, dtype=float)
    hi = lo if upper is None else np.asarray(upper, dtype=float)
    a, b = bounds
    if (lo.ndim != 1 or len(lo) < 2 or hi.shape != lo.shape
            or not np.isfinite(lo).all() or not np.isfinite(hi).all()
            or not a < b or not 0 < alpha < 1 or type(comparisons) is not int
            or comparisons < 1 or np.any(lo > hi) or np.any(lo < a) or np.any(hi > b)):
        raise ValueError('Неверные ограниченные независимые оценки')
    half = (b-a) * math.sqrt(math.log(2*comparisons/alpha)/(2*len(lo)))
    return dict(lower=max(a, float(lo.mean())-half), upper=min(b, float(hi.mean())+half),
                mean_bounds=[float(lo.mean()), float(hi.mean())], half_width_bound=half,
                randomizations=len(lo), method='hoeffding_bonferroni', comparisons=comparisons,
                alpha=alpha, independence_required=True)


def aggregate(rows, design, *, hosts=None, phase='pilot'):
    """Прямые выборки p=q: веса равны 1; отсутствие строки не меняет знаменатель."""
    hosts = design.development if hosts is None else hosts
    if not hosts or len(set(hosts)) != len(hosts) or not set(hosts) <= set(design.main_hosts+design.control_hosts):
        raise ValueError('Неверный набор хозяев')
    groups = {}
    domains = {}
    for raw in rows:
        row = EnsembleRow.model_validate(raw)
        if (row.host not in hosts or row.measure not in design.measures
                or row.scenario_id != design.scenario_id or row.horizon_seconds != design.horizon_seconds
                or row.seed != design.seed(row.randomization, phase=phase) or row.weight != 1.0
                or row.index >= 2**design.power):
            raise ValueError('Несовместимая постановка/seed/вес')
        if row.host in domains and domains[row.host] != row.domain_id:
            raise ValueError('Смешение областей')
        domains[row.host] = row.domain_id
        key = (row.host, row.measure, row.randomization)
        group = groups.setdefault(key, {})
        if row.index in group:
            raise ValueError('Повторная запись старта')
        group[row.index] = row
    expected = set(itertools.product(hosts, design.measures, range(design.randomizations)))
    if set(groups) != expected or any(len(g) != 2**design.power for g in groups.values()):
        raise ValueError('Неполный знаменатель ансамбля')
    result = {}
    for host, measure in itertools.product(hosts, design.measures):
        lower, upper = [], []
        for k in range(design.randomizations):
            values = list(groups[host, measure, k].values())
            kept = sum(r.outcome == 'survived' for r in values)
            unknown = sum(r.outcome == 'unresolved' for r in values)
            lower.append(kept/len(values))
            upper.append((kept+unknown)/len(values))
        result[host, measure] = (np.array(lower), np.array(upper))
    return result


def compare_hypotheses(estimates, design):
    required = set(itertools.product(design.main_hosts+design.control_hosts, design.measures))
    if set(estimates) != required:
        raise ValueError('H1/H2 требуют полного заранее заданного набора хозяев')
    h1 = []
    for measure in design.measures:
        main = np.mean([estimates[h, measure] for h in design.main_hosts], axis=0)
        control = np.mean([estimates[h, measure] for h in design.control_hosts], axis=0)
        interval = bounded_interval(main[0]-control[1], main[1]-control[0], bounds=(-1, 1),
                                    alpha=design.alpha, comparisons=len(design.measures))
        h1.append(dict(measure=measure, **interval))
    h1_status = ('supported' if all(i['lower'] > design.effect for i in h1) else
                 'contradicted' if any(i['upper'] < design.effect for i in h1) else 'inconclusive')
    pairs = list(itertools.combinations(design.main_hosts, 2))
    h2, states = [], []
    for left, right in pairs:
        intervals = []
        for measure in design.measures:
            a, b = estimates[left, measure], estimates[right, measure]
            interval = bounded_interval(a[0]-b[1], a[1]-b[0], bounds=(-1, 1),
                                        alpha=design.alpha, comparisons=len(pairs)*len(design.measures))
            intervals.append(dict(measure=measure, **interval))
        inversion = any(i['lower'] > design.effect for i in intervals) and any(i['upper'] < -design.effect for i in intervals)
        same = all(i['lower'] > 0 for i in intervals) or all(i['upper'] < 0 for i in intervals)
        equal = all(i['lower'] >= -design.effect and i['upper'] <= design.effect for i in intervals)
        states.append('contradicted' if inversion else 'supported' if same or equal else 'inconclusive')
        h2.append(dict(left=left, right=right, intervals=intervals, status=states[-1]))
    return dict(H1=dict(status=h1_status, comparisons=h1), H2=dict(
        status='contradicted' if 'contradicted' in states else 'supported' if set(states) == {'supported'} else 'inconclusive',
        comparisons=h2))


def geometric_fraction(domain, measure, coefficient):
    if measure not in MEASURES or not 0 < coefficient <= 1:
        raise ValueError('Неверная геометрическая модель')
    normal, emax, _ = domain_normalization(domain, measure)
    def integrand(e):
        low = domain.inner_km/(1-e)
        high = min(domain.hill_km, coefficient*domain.hill_km/(1+e))
        if high <= low:
            return 0.0
        if measure == MEASURES[0]:
            return high-low
        if measure == MEASURES[1]:
            return math.log(high/low)
        return e*2/3*(high**1.5-low**1.5)
    return quad(integrand, 0, emax, epsabs=0, epsrel=1e-10)[0]/normal


def fit_geometry(domains, estimates, design):
    if set(domains) != set(design.development):
        raise ValueError('H3 подбирается только на разработочных хозяевах')
    keys = set(itertools.product(design.development, design.measures))
    if set(estimates) != keys:
        raise ValueError('Неполная или загрязнённая разработочная выборка')
    if any(not np.array_equal(lo, hi) for lo, hi in estimates.values()):
        return dict(status='inconclusive', reason='unresolved_development_outcomes', coefficient=None)
    observed = {key: float(value[0].mean()) for key, value in estimates.items()}
    scores = [(float(np.mean([(geometric_fraction(domains[h], m, c)-observed[h, m])**2
                             for h, m in sorted(keys)])), c)
              for c in [round(0.1+0.05*i, 2) for i in range(17)]]
    loss, coefficient = min(scores)
    return dict(status='development_fit', coefficient=coefficient, mse=loss,
                constants={m: float(np.mean([observed[h, m] for h in design.development])) for m in design.measures},
                independent_validation=False)


def evaluate_geometry(domains, estimates, fitted, design):
    if set(domains) != set(design.holdout) or fitted.get('status') != 'development_fit':
        raise ValueError('Нужны замороженная модель и полный отложенный набор')
    if set(estimates) != set(itertools.product(design.holdout, design.measures)):
        raise ValueError('Неполная независимая проверка')
    intervals = []
    for measure in design.measures:
        low, high = [], []
        for host in design.holdout:
            prediction = geometric_fraction(domains[host], measure, fitted['coefficient'])
            constant = fitted['constants'][measure]
            lo, hi = estimates[host, measure]
            # Разность квадратов линейна по неизвестной истинной доле; нет смещения y^2.
            a = prediction**2-constant**2-2*(prediction-constant)*lo
            b = prediction**2-constant**2-2*(prediction-constant)*hi
            low.append(np.minimum(a, b))
            high.append(np.maximum(a, b))
        intervals.append(dict(measure=measure, **bounded_interval(np.mean(low, axis=0),
            np.mean(high, axis=0), bounds=(-1, 1), alpha=design.alpha, comparisons=len(design.measures))))
    status = ('supported' if all(x['upper'] < 0 for x in intervals) else
              'contradicted' if any(x['lower'] > 0 for x in intervals) else 'inconclusive')
    return dict(status=status, comparisons=intervals, scope='fixed_holdout_hosts_not_population_of_hosts')


def required_randomizations(*, half_width=0.01, alpha=0.05, comparisons=1, range_width=1.0):
    if not 0 < half_width < range_width or not 0 < alpha < 1 or comparisons < 1:
        raise ValueError('Неверный бюджет точности')
    return math.ceil(range_width**2*math.log(2*comparisons/alpha)/(2*half_width**2))
