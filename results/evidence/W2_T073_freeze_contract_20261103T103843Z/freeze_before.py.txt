"""Freeze требует доказательств, а статус draft не открывает научные ворота."""
from submoon_research.provenance import InputLedger
from submoon_research.contracts.w1 import W1Design


def validate_freeze(root, design):
    required=('inputs','generator','domain','measures','pilot','hypotheses','split','predictions',
        'comparisons','intervals','stopping','failures','horizons','numeric_protocol','w1_design')
    if design.get('status')!='ready_for_freeze' or any(not design.get(k) for k in required):
        raise ValueError('Неполный протокол W1: freeze запрещён')
    ledger=InputLedger(root)
    specification = W1Design.model_validate(design['w1_design'])
    if specification.status != 'ready_for_freeze':
        raise ValueError('Исполняемая постановка W1 не готова к freeze')
    for entry in design['inputs']:
        ledger.bind(entry['path'],entry['sha256'])
        ledger.read(entry['path'])
    pilot=ledger.json(design['pilot']['manifest'])
    if pilot['status']!='completed' or pilot['validation']['status']!='passed' or pilot.get('data_kind') in ('synthetic','source_audit','synthetic_analytic_control'):
        raise ValueError('Для freeze нужен проверенный реальный разработочный пилот')
    admission=pilot.get('scientific_admission',{})
    if not all(admission.get(k) is True for k in ('audited_physics','validated_generator','validated_dynamics','validated_events')) or pilot.get('resources',{}).get('integration_calls',0)<=0:
        raise ValueError('Входные/динамические ворота реального пилота не подтверждены')
    if not design['pilot'].get('cost_evidence') or not design['pilot'].get('variance_evidence'):
        raise ValueError('Стоимость и дисперсия пилота не получены')
    ledger.read(design['pilot']['cost_evidence'])
    ledger.read(design['pilot']['variance_evidence'])
    if pilot.get('data_kind') != 'real_nominal_development_pilot' or admission.get('full_horizon_completed') is not True:
        raise ValueError('Короткий benchmark не заменяет полный пилот на общем T')
    expected = dict(design_id=specification.design_id,
                    horizon_seconds=specification.horizon_seconds,
                    scenario_id=specification.scenario_id,
                    randomizations=specification.randomizations,
                    points_per_randomization=2**specification.power,
                    hosts=specification.development, measures=specification.measures)
    if pilot.get('sampling_design') != expected:
        raise ValueError('Пилот относится к другой постановке')
    for key in ('cost_evidence', 'variance_evidence'):
        evidence = ledger.json(design['pilot'][key])
        if evidence.get('run_id') != pilot['run_id'] or evidence.get('sampling_design') != expected or evidence.get('status') != 'validated':
            raise ValueError('Стоимость/дисперсия не подтверждают данный пилот')
    # Подписи всех использованных входов/кода и артефактов должны согласоваться.
    from pathlib import PurePosixPath
    parent = PurePosixPath(design['pilot']['manifest']).parent
    for path, digest in pilot.get('artifacts_sha256', {}).items():
        ledger.bind((parent/path).as_posix(), digest)
        ledger.read((parent/path).as_posix())
    if not pilot.get('artifacts_sha256') or not pilot.get('code_sha256'):
        raise ValueError('Нет полного provenance пилота')
    for path, digest in pilot['code_sha256'].items():
        ledger.bind((parent/'code'/path).as_posix(), digest)
        ledger.read((parent/'code'/path).as_posix())
    for path, digest in pilot.get('inputs_sha256', {}).items():
        ledger.bind(path, digest)
        ledger.read(path)
    for key in ('numeric_protocol', 'intervals', 'predictions'):
        evidence = ledger.json(design[key]['evidence_path'])
        if evidence.get('status') != 'validated' or evidence.get('design_id') != specification.design_id:
            raise ValueError('Не приняты численный режим/интервалы/предсказания')
        if key == 'numeric_protocol' and (evidence.get('horizon_seconds') != specification.horizon_seconds
                or not all(evidence.get(flag) is True for flag in ('events_validated', 'long_dynamics_validated', 'restart_validated'))):
            raise ValueError('Численный допуск не покрывает горизонт и события')
        if key == 'predictions' and (evidence.get('coefficient') is None or not evidence.get('holdout_predictions')
                or evidence.get('holdout_revealed') is not False):
            raise ValueError('Количественные предсказания не зарегистрированы до раскрытия')
    split=design['split']
    if set(split['development']) & set(split['holdout']) or not split['holdout'] or split.get('contamination_log'):
        raise ValueError('Независимый split не готов')
    if split['development'] != specification.development or split['holdout'] != specification.holdout:
        raise ValueError('Split отличается от исполняемого дизайна')
    if set(design['hypotheses'])!={'H1','H2','H3'} or any(set(h['outcomes'])!={'supported','contradicted','inconclusive'} for h in design['hypotheses'].values()):
        raise ValueError('Не зафиксированы допустимые исходы H1–H3')
    return dict(status='eligible_for_freeze',inputs_sha256=ledger.used,production_allowed=False)
