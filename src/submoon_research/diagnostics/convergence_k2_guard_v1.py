"""Изолированный fail-closed guard K2; ретроспективный прототип, не допуск.

До отдельного согласования непрерывного покрытия/геометрии положительные K2
не выдаются. Исторические v1/v2 и входы не меняются; остальные классы не
проверяются этим слоем. Не использовать для выбора режима или production.
"""
from __future__ import annotations

from submoon_research.diagnostics import convergence_v2_guard as v2

VERSION = 'W2-PH3-k2-fail-closed-prototype-v1'


def classify(case, thresholds, directions, archive_b=None):
    """Применить v2 и понизить неподтверждённый K2 в K4 без переклассификации.

    Вызывающий код отвечает за проверку SHA, состава и исходов. Сохранённые
    диагностические точки не доказывают непрерывную близость к событию.
    """
    prior = v2.classify(case, thresholds, directions, archive_b=archive_b)
    result = dict(prior)
    result['flags'] = list(prior['flags'])
    result['prior_v2_class'] = prior['cls']
    result['version'] = VERSION
    result['k2_status'] = 'not_assessed'
    if prior['cls'] == 'K2':
        pairs = prior['original'].get('k2', [])
        unknown = any(
            not v2.determined(case['rows'][code])
            for pair in pairs if pair.get('agree_to_event')
            for code in pair['pair']
        )
        result['flags'].append('k2_event_coverage_not_validated')
        if unknown:
            result['flags'].append('k2_unknown_or_failed_outcome')
        result['cls'] = 'K4'
        result['confidence'] = None
        result['k2_status'] = 'insufficient_evidence'
    result['note'] = ('Консервативный ретроспективный guard: K2 не допускается; '
                      'K0/K1/K3 и выбор режима данным слоем не проверены.')
    return result
