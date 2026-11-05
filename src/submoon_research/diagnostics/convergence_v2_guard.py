"""W2-T065: изолированный прототип защит K1/K3; НЕ полный анализатор и НЕ допуск.

Научные данные уже раскрыты. Вход (включая archive_b) должен быть заранее
проверен вызывающим кодом на SHA, точную идентичность старта/физики/горизонта.
Здесь проверки provenance нет; не использовать это API для production решений.
"""
from __future__ import annotations

from collections import Counter

from submoon_research.diagnostics import convergence as v1

VERSION = 'W2-PH3-v2-guard-prototype'


def determined(row):
    return row.get('run_status') == 'completed' and row.get('outcome') in ('survived', *v1.TERMINAL)


def perturbation_grid(case, directions):
    """Полная сетка и известные исходы; unknown, ошибки и дубли явно возвращаются."""
    rows = case.get('perturb_rows') or []
    scales = [float(scale) for scale in case['scales']]
    if (not scales or not directions or len(set(scales)) != len(scales)
            or len(set(directions)) != len(directions) or any(scale <= 0 for scale in scales)):
        raise ValueError('Недопустимая спецификация сетки возмущений')
    expected = {(scale, direction) for scale in scales for direction in directions}
    counts = Counter((float(r['perturbation']['scale']), r['perturbation']['direction']) for r in rows)
    missing = sorted(expected - counts.keys())
    extra = sorted(counts.keys() - expected)
    duplicates = sorted(k for k, n in counts.items() if n > 1)
    unknown = sum(not determined(r) for r in rows)
    base = case['rows'].get(case.get('perturb_family'))
    base_known = base is not None and determined(base)
    return dict(complete=not (missing or extra or duplicates or unknown) and base_known,
                expected=len(expected), rows=len(rows), missing=missing, extra=extra,
                duplicates=duplicates, unknown=unknown, base_known=base_known)


def physical_sensitivity(case, threshold, directions):
    """Физические flips и диапазон времён *внутри* исхода; отдельно сдвиг от базы."""
    base = case['rows'].get(case.get('perturb_family'))
    result = {}
    for scale in case['scales']:
        rows = [r for r in case.get('perturb_rows', []) if r['perturbation']['scale'] == scale]
        known = [r for r in rows if determined(r)]
        by_label = {}
        for r in known:
            by_label.setdefault(r['outcome'], []).append(v1.outcome_time(r))
        spread = max(((max(t) - min(t)) / case['p0'] for t in by_label.values()), default=None)
        flips = sum(r['outcome'] != base['outcome'] for r in known) if base and determined(base) else None
        same = [r for r in known if base and determined(base) and r['outcome'] == base['outcome']]
        shift = max((abs(v1.outcome_time(r) - v1.outcome_time(base)) / case['p0'] for r in same), default=None)
        result[f'{scale:g}'] = dict(n=len(rows), known=len(known), unknown=len(rows) - len(known),
                                   physical_flips=flips, spread_p0=spread, max_shift_p0=shift,
                                   outcomes={k: len(v) for k, v in sorted(by_label.items())})
    return result


def label_nonconvergence(case):
    """Последние две доступные ступени одного семейства имеют разные определённые исходы."""
    for codes in case['ladders'].values():
        present = [case['rows'][c] for c in codes if c in case['rows']]
        if len(present) >= 2 and all(determined(r) for r in present[-2:]):
            if present[-2]['outcome'] != present[-1]['outcome']:
                return True
    return False


def _check_against_limit(row, limit, p0, thr):
    return (determined(row) and row['outcome'] == limit['label'] and
            abs(v1.outcome_time(row) - limit['time']) <= thr['event_time_p0'] * p0)


def classify(case, thr, directions, archive_b=None):
    """Консервативный экспериментальный разбор. Не решает исход серии и не проверяет SHA."""
    original = v1.classify(case, thr)
    grid = perturbation_grid(case, directions)
    stats = physical_sensitivity(case, thr, directions)
    result = dict(original=original, cls=original['cls'], confidence=original['confidence'],
                  perturbation_grid=grid, physical_sensitivity=stats,
                  label_nonconvergence=label_nonconvergence(case), flags=[], version=VERSION,
                  note='ретроспективный прототип; provenance и финальное решение не проверены')
    if result['cls'] in ('K1', 'K3') and not grid['complete']:
        result['flags'].append('perturbations_incomplete_or_unknown')
        result['cls'], result['confidence'] = 'K4', None
    if result['cls'] == 'K1':
        limit = original['dop853'] if original['coarse'] == 'D' else original['ias15']
        controls = [case['rows'][k] for k in case['ladders']['ias15'] if k in case['rows']]
        if archive_b is not None:
            controls.append(archive_b)
        if original['coarse'] == 'D' and not controls:
            result['flags'].append('independent_control_missing')
        elif original['coarse'] == 'D' and any(not _check_against_limit(r, limit, case['p0'], thr)
                                                for r in controls):
            result['flags'].append('independent_control_unknown_or_contradictory')
        if result['flags']:
            result['cls'], result['confidence'] = 'K4', None
        elif original['families_agree'] is not True:
            result['confidence'] = 'medium'
    if result['cls'] == 'K3':
        s_star = original['s_star']
        sensitive = [f'{s:g}' for s in case['scales'] if s <= s_star and (
            (stats[f'{s:g}']['physical_flips'] or 0) >= thr['k3_min_directions'] or
            (stats[f'{s:g}']['spread_p0'] or 0) > thr['k3_time_spread_p0'])]
        if not result['label_nonconvergence'] or not sensitive:
            result['flags'].append('literal_k3_not_established')
            result['cls'], result['confidence'] = 'K4', None
        else:
            result['confidence'] = 'high' if f"{min(case['scales']):g}" in sensitive else 'medium'
    if case.get('high_e') and result['confidence'] == 'high':
        result['flags'].append('high_e_confidence_capped')
        result['confidence'] = 'medium'
    return result
