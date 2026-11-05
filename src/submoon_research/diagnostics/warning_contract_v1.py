"""Изолированный прототип политики предупреждений, не захватчик и не runner.

Вызывающая сторона отвечает за полноту наблюдения, достоверность снимка и SHA
полного checkpoint. Этот модуль не подключён к историческому B или production.
"""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

SCHEMA = 'W2-warning-ledger-prototype-v1'
POLICY = 'W2-warnings-fail-closed-v1'
PHASES = {'initialization', 'cache', 'integration', 'events', 'checkpoint', 'unknown'}
RUN_STATUSES = {'completed', 'failed', 'partial'}


def digest(value):
    """Детерминированный SHA канонического JSON; не подпись происхождения."""
    data = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode('utf-8')).hexdigest()


def policy_fingerprint(implementation_sha256, capture_sha256):
    """Версия политики и SHA обеих реализаций входят в контракт режима."""
    _sha(implementation_sha256)
    _sha(capture_sha256)
    return digest(dict(policy=POLICY, implementation_sha256=implementation_sha256,
                       capture_sha256=capture_sha256))


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('Ожидается строчный SHA-256')


def _event(event):
    if not isinstance(event, dict) or set(event) != {'category', 'message', 'phase', 'time'}:
        raise ValueError('Неверная структура предупреждения')
    if any(not isinstance(event[k], str) or not event[k] for k in ('category', 'message')):
        raise ValueError('Пустая категория или сообщение')
    if event['phase'] not in PHASES:
        raise ValueError('Неизвестная фаза')
    t = event['time']
    if t is not None and (isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t) or t < 0):
        raise ValueError('Некорректное время')


def assess(*, engine_status, capture_complete, overflow, events, policy_sha256, prior=None,
           expected_prior_sha256=None):
    """Объединить предыдущий ledger и новый наблюдаемый фрагмент, без изменения входов.

    При продолжении требуется внешний SHA предыдущего ledger; пропущенный,
    повреждённый или несовместимый предок отвергается. Запись checkpoint/атомарность
    и определение физического исхода остаются обязанностью будущего runner.
    """
    _sha(policy_sha256)
    if engine_status not in RUN_STATUSES or type(capture_complete) is not bool or type(overflow) is not bool:
        raise ValueError('Некорректный статус или полнота захвата')
    if not isinstance(events, list):
        raise ValueError('Список предупреждений обязателен')
    for event in events:
        _event(event)
    if prior is None:
        if expected_prior_sha256 is not None:
            raise ValueError('Нет предшествующего ledger')
        accumulated = []
        all_complete = True
        any_overflow = False
    else:
        _sha(expected_prior_sha256)
        if digest(prior) != expected_prior_sha256:
            raise ValueError('SHA предшествующего ledger не совпадает')
        if (not isinstance(prior, dict) or set(prior) != {'schema', 'policy_sha256', 'capture_complete',
                                                           'overflow', 'events', 'engine_status', 'assessment'}
                or prior['schema'] != SCHEMA or prior['policy_sha256'] != policy_sha256):
            raise ValueError('Несовместимый ledger')
        if type(prior['capture_complete']) is not bool or type(prior['overflow']) is not bool:
            raise ValueError('Некорректная полнота предыдущего ledger')
        if not isinstance(prior['events'], list) or prior['engine_status'] != 'partial':
            raise ValueError('Продолжать можно только partial ledger')
        for item in prior['events']:
            _event(item)
        expected = _assessment(prior['engine_status'], prior['capture_complete'], prior['overflow'], prior['events'])
        if prior['assessment'] != expected:
            raise ValueError('Несогласованный предыдущий verdict')
        accumulated = deepcopy(prior['events'])
        all_complete = prior['capture_complete']
        any_overflow = prior['overflow']
    accumulated.extend(deepcopy(events))
    all_complete = all_complete and capture_complete
    any_overflow = any_overflow or overflow
    verdict = _assessment(engine_status, all_complete, any_overflow, accumulated)
    return dict(schema=SCHEMA, policy_sha256=policy_sha256, capture_complete=all_complete,
                overflow=any_overflow, events=accumulated, engine_status=engine_status, assessment=verdict)


def _assessment(status, complete, overflow, events):
    reasons = []
    if status != 'completed':
        reasons.append('engine_' + status)
    if not complete:
        reasons.append('capture_incomplete')
    if overflow:
        reasons.append('capture_overflow')
    if events:
        reasons.append('warnings_present')
    return dict(admissible=not reasons, reasons=reasons,
                physical_outcome_policy='unresolved' if reasons else 'preserve_engine_outcome')
