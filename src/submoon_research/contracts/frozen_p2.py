"""Закреплённые входы P2: доверенный SHA манифеста задаётся извне, не из units.json.

Проверка генератора — отдельная операция. Чтение этого артефакта не вызывает
генератор и не выводит доверие из полей самого проверяемого документа.
"""
from __future__ import annotations

from pathlib import Path

from submoon_research.contracts.p2_files import (
    atomic_json, canonical, contract_digest, file_digest, read_json, validate_units_document,
)

SCHEMA = 'p2-frozen-input-v1'


def validate_frozen_document(doc):
    validate_units_document(doc, doc)
    if 'contract_sha256' not in doc:
        raise ValueError('Для frozen inputs нужен полный контракт, не legacy units.json')
    if (doc['plan'] != 'W2-P006' or doc['stage'] != 'P2-2a'
            or doc['generator_numerics'] not in ('exact-fsum-v1', 'legacy-blas')
            or doc['contract'].get('version') != 'p2-input-v1'):
        raise ValueError('Неподдерживаемая версия frozen inputs')
    # Фиксированная схема P2-2-v1, не регенерация float-векторов.
    layout = [(h, m) for h in ('iapetus', 'ganymede', 'himalia')
              for m in ('uniform_a_e_cos_i_phases', 'uniform_log_a', 'canonical_volume')]
    params = doc['params']
    if set(params) != {'per_cell', 'b_per_cell', 'unit_start', 'years', 'b_years'}:
        raise ValueError('Неверные параметры frozen inputs')
    if any(type(params[n]) is not int for n in ('per_cell', 'b_per_cell', 'unit_start')):
        raise ValueError('Неверный тип параметров выборки')
    if not (params['per_cell'] > 0 and params['unit_start'] >= 0
            and 0 <= params['b_per_cell'] <= params['per_cell']):
        raise ValueError('Неверный размер выборки')
    if (not params['years'] or any(type(y) not in (int, float) or y <= 0 for y in params['years'])
            or sorted(set(params['years'])) != params['years']
            or sorted(set(params['b_years'])) != params['b_years']
            or not set(params['b_years']) <= set(params['years'])):
        raise ValueError('Неверные горизонты')
    fields = {'orbit_id', 'host', 'measure', 'cell', 'unit', 'seed', 'a_km', 'eccentricity',
              'cos_i', 'a_over_rh', 'state', 'gms', 'figures', 'radii', 'hill_km',
              'escape_window', 'period_seconds'}
    items = doc['units']
    if len(items) != 9*params['per_cell']:
        raise ValueError('Неполный состав frozen inputs')
    cells = {}
    for u in items:
        if set(u) != fields:
            raise ValueError('Неверная схема единицы frozen inputs')
        if (type(u['eccentricity']) not in (int, float) or not 0 <= u['eccentricity'] <= 0.3
                or type(u['cos_i']) not in (int, float) or not -1 <= u['cos_i'] <= 1):
            raise ValueError('Неверные элементы стартовой области')
        if not 0 <= u['cell'] < 9 or not params['unit_start'] <= u['unit'] < params['unit_start']+params['per_cell']:
            raise ValueError('Единица вне заявленной области')
        key = (u['host'], u['measure'])
        if key != layout[u['cell']] or u['seed'] != 205610051+9*u['unit']+u['cell']:
            raise ValueError('Ячейка или seed не соответствуют схеме P2-2-v1')
        if u['cell'] in cells and cells[u['cell']] != key:
            raise ValueError('Неоднозначная ячейка')
        cells[u['cell']] = key
        if u['orbit_id'] != f"{u['host']}:{u['measure']}:{u['unit']:04d}":
            raise ValueError('orbit_id не соответствует единице')
    if len(cells) != 9 or len(set(cells.values())) != 9:
        raise ValueError('Неверный состав ячеек')
    if len({(u['cell'], u['unit']) for u in items}) != len(items):
        raise ValueError('Повтор единицы ячейки')
    return doc


def manifest_for(units_path, document, provenance):
    validate_frozen_document(document)
    if not isinstance(provenance, dict) or not provenance.get('kind'):
        raise ValueError('Отсутствует происхождение входов')
    return dict(schema=SCHEMA, units_file='units.json', units_file_sha256=file_digest(units_path),
                units_sha256=document['units_sha256'], contract_sha256=contract_digest(document),
                provenance=provenance)


def save_frozen_inputs(directory, document, provenance):
    """Новый самостоятельный артефакт; источник не изменяется."""
    validate_frozen_document(document)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    atomic_json(directory/'units.json', document)
    manifest = manifest_for(directory/'units.json', document, provenance)
    atomic_json(directory/'inputs_manifest.json', manifest)
    return file_digest(directory/'inputs_manifest.json')


def load_frozen_inputs(manifest_path, expected_sha256, *, params=None, numerical_contract=None):
    """Сначала внешний trust anchor, затем SHA файла/полного контракта и схема."""
    manifest_path = Path(manifest_path)
    if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
        raise ValueError('Нужен независимо закреплённый SHA-256 inputs manifest')
    if file_digest(manifest_path) != expected_sha256:
        raise ValueError('SHA inputs manifest не совпал с внешним якорем')
    manifest = read_json(manifest_path)
    if (not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA
            or manifest.get('units_file') != 'units.json'
            or not isinstance(manifest.get('provenance'), dict) or not manifest['provenance'].get('kind')):
        raise ValueError('Неверный манифест frozen inputs')
    units_path = manifest_path.parent/'units.json'
    if file_digest(units_path) != manifest['units_file_sha256']:
        raise ValueError('SHA файла frozen inputs не совпал')
    document = validate_frozen_document(read_json(units_path))
    if (document['units_sha256'] != manifest['units_sha256']
            or document['contract_sha256'] != manifest['contract_sha256']):
        raise ValueError('SHA содержимого frozen inputs не совпал с манифестом')
    if params is not None and canonical(params) != canonical(document['params']):
        raise ValueError('Frozen inputs не соответствуют параметрам задания')
    if numerical_contract is not None and canonical(numerical_contract) != canonical(document['contract']):
        raise ValueError('Frozen inputs не соответствуют численному контракту')
    return document, manifest


def check_generator(manifest_path, expected_sha256, generate_document):
    """Отдельная регрессия генератора; никогда не используется как приёмка расчёта."""
    document, _ = load_frozen_inputs(manifest_path, expected_sha256)
    regenerated = generate_document(document)
    return dict(passed=canonical(document) == canonical(regenerated),
                frozen_contract_sha256=document['contract_sha256'],
                generated_contract_sha256=regenerated['contract_sha256'])


def copied_provenance(manifest_path, expected_sha256):
    return dict(kind='frozen-input-copy', source_manifest=str(Path(manifest_path)),
                source_manifest_sha256=expected_sha256)
