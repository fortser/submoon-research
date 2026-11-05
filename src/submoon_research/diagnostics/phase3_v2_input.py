"""W2-T065: read-only вход для ретроспективного анализа Фазы 3.

SHA отчёта W2-T064 обязателен извне. Файлы читаются в проверенный снимок
байтов, а не повторно из изменяемого каталога. Научная классификация не делается.
Проверка обеспечивает соответствие закреплённому локальному снимку, не истину
физики, независимость audit.json или безопасность злонамеренного процесса ОС.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath

import numpy as np
import yaml

from submoon_research.diagnostics import phase3_audit as audit

RUNS = {
    'P0': 'runs/W2-PH3-P0-20261026T113011Z',
    'P1': 'runs/W2-PH3-P1-20261028T091305Z',
    'perturb': 'runs/W2-PH3-perturb-20261028T093615Z',
}
SCHEMA = 'w2-phase3-audit-v1'
YEAR = audit.YEAR


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _json(data):
    return audit.strict_loads(data.decode('utf-8'))


def _jsonl(data):
    if not data.endswith(b'\n'):
        raise ValueError('JSONL: отсутствует завершающий перевод строки')
    return [_json(line) for line in data.splitlines() if line.strip()]


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def capture(root, inventory):
    """Зафиксировать байты всех перечисленных файлов по обязательным SHA и размеру."""
    root = Path(root).resolve(strict=True)
    _require(isinstance(inventory, dict) and bool(inventory), 'Пустой инвентарь')
    snapshot = {}
    for name, entry in inventory.items():
        posix = PurePosixPath(name)
        _require(isinstance(name, str) and name and not posix.is_absolute() and
                 all(part not in ('..', '.', '') for part in name.split('/')) and
                 '\\' not in name and ':' not in name, 'Недопустимый путь в инвентаре')
        file = (root / name).resolve(strict=True)
        _require(file.is_relative_to(root) and file.is_file(), 'Путь выходит за пределы проекта: ' + name)
        data = file.read_bytes()
        _require(isinstance(entry, dict) and set(entry) == {'sha256', 'bytes'} and
                 isinstance(entry['sha256'], str) and len(entry['sha256']) == 64 and
                 type(entry['bytes']) is int and entry['bytes'] == len(data) and
                 _hash(data) == entry['sha256'], 'Несовпадение SHA/размера: ' + name)
        snapshot[name] = data
    return snapshot


def _get(snapshot, name):
    _require(name in snapshot, 'Файл отсутствует в закреплённом инвентаре: ' + name)
    return snapshot[name]


def _units(data):
    doc = _json(data)
    units = doc['units']
    by_id = {u['orbit_id']: u for u in units}
    _require(len(by_id) == len(units), 'Повтор orbit_id в units')
    legacy = hashlib.sha256(json.dumps(
        [(u['orbit_id'], u['seed'], u['state']) for u in units], default=float).encode()).hexdigest()
    _require(doc['units_sha256'] == legacy, 'Неверный units_digest')
    return by_id


def _sample(snapshot, row, run, unit):
    name = row['samples_file']
    _require(name == f"{run}/samples/{row['job_fingerprint'][:24]}.npz", 'Чужой путь выборки')
    data = _get(snapshot, name)
    _require(_hash(data) == row['samples_sha256'], 'SHA выборки не совпадает с записью')
    with np.load(io.BytesIO(data), allow_pickle=False) as z:
        _require(set(z.files) == {'times', 'probe', 'full_times', 'full_state'}, 'Состав NPZ неверен')
        t, p, ft, full = (z[k] for k in ('times', 'probe', 'full_times', 'full_state'))
        _require(len(t) == row['samples_count'] and len(t) > 0 and p.shape == (len(t), 6) and
                 len(full) == len(ft) and full.ndim == 2 and full.shape[1] == len(unit['state']) and
                 all(np.isfinite(x).all() for x in (t, p, ft, full)) and
                 t[0] == 0 and np.all(np.diff(t) > 0) and np.all(np.diff(ft) > 0) and
                 t[-1] <= row['last_valid_time'], 'Некорректная выборка')
        idx = np.searchsorted(t, ft)
        _require(np.all(idx < len(t)), 'Полная сетка вне выборки')
        _require(np.array_equal(t[idx], ft) and np.array_equal(p[idx], full[:, -6:]),
                 'Несогласованные полные состояния')
        initial = audit.initial_state(unit, row['perturbation'])
        _require(len(ft) > 0 and ft[0] == 0 and np.allclose(full[0], initial, rtol=3e-15, atol=1e-15)
                 and np.array_equal(full[0][:-6], np.asarray(unit['state'][:-6])), 'Начальное состояние NPZ')
        return t.copy(), p.copy()


def _contact_body_valid(outcome, event, unit):
    if outcome not in ('host_contact', 'other_contact'):
        return True
    body = event.get('body') if event else None
    return type(body) is int and 0 <= body < len(unit['gms']) and (
        (outcome == 'host_contact' and body == 0) or (outcome == 'other_contact' and body > 0))


def _validate_plan(manifest, cfg, case_list, units, rows):
    specs = audit.expected_specs(cfg, case_list, manifest['block'], manifest['b_horizon_years'])
    expected = {audit.fingerprint(s, units[s['orbit_id']], cfg, manifest['implementations']): s for s in specs}
    _require(len(expected) == len(specs), 'Повтор/коллизия задания в ожидаемом плане')
    planned = {p['fingerprint']: {k: v for k, v in p.items() if k != 'fingerprint'} for p in manifest['plan']}
    _require(len(planned) == len(manifest['plan']) and planned == expected, 'План не совпадает с постановкой')
    actual = {r['job_fingerprint']: r for r in rows}
    _require(len(actual) == len(rows) and set(actual) == set(expected), 'Пропуск, повтор или чужое задание')
    for fp, spec in expected.items():
        r = actual[fp]
        _require(all(r.get(k) == v for k, v in spec.items()), 'Строка не соответствует fingerprint/плану')
        _require(r['run_status'] == 'completed' and r['error'] is None, 'Не завершённое задание')
        _require(r['family'] == cfg['modes'][r['code']]['family'], 'Неверное семейство')
        last = r['last_valid_time']
        horizon = r['years'] * YEAR
        _require(math.isfinite(last) and 0 <= last <= horizon and
                 (r['outcome'] != 'survived' or last == horizon), 'Неверное последнее время')
        event_types = {'host_contact': 'reference_contact', 'other_contact': 'reference_contact',
                       'operational_escape': 'operational_escape'}
        event = r['event']
        _require((event is None and r['outcome'] in ('survived', 'unresolved')) or
                 (event is not None and event['event'] == event_types.get(r['outcome']) and
                  event['time'] == last), 'Несогласованное событие')
        _require(_contact_body_valid(r['outcome'], event, units[r['orbit_id']]), 'Неверное тело контакта')
        cp = r['checkpoint']
        _require(cp is not None and cp['time'] == last and cp['state'] == r['final_state'] and
                 cp['escape'] is not None, 'Несогласованный checkpoint')
        unit = units[r['orbit_id']]
        _require(r['period_seconds'] == unit['period_seconds'] and r['a_km'] == unit['a_km'],
                 'Строка/единица не совпадают')
    return actual


def _archive(snapshot, cfg):
    bpath = cfg['archives']['b144']['run']
    manifest_bytes = _get(snapshot, bpath + '/manifest.json')
    _require(_hash(manifest_bytes) == cfg['archives']['b144']['manifest_sha256'], 'Архивный B: SHA manifest')
    manifest = _json(manifest_bytes)
    units_bytes = _get(snapshot, bpath + '/units.json')
    _require(_hash(units_bytes) == manifest['units_file_sha256'] == cfg['archives']['b144']['units_file_sha256'],
             'Архивный B: SHA units')
    _require(manifest['b_implementation'] == cfg['expected_b_implementation'], 'Архивный B: версия реализации')
    units = _units(units_bytes)
    jobs_bytes = _get(snapshot, bpath + '/jobs.jsonl')
    _require(_hash(jobs_bytes) == cfg['archives']['b144']['jobs_sha256'], 'Архивный B: SHA jobs')
    rows = _jsonl(jobs_bytes)
    plan = {(x['orbit_id'], float(x['years'])) for x in manifest['plan']}
    indexed = {(r['orbit_id'], float(r['years'])): r for r in rows}
    required = {(oid, years) for oid in units for years in (100.0, 1000.0)}
    _require(len(units) == 144 and len(plan) == len(manifest['plan']) == 288 and
             len(indexed) == len(rows) == 288 and set(indexed) == plan == required,
             'Архивный B: неполный план или дубли')
    impl = {'B': manifest['b_implementation']}
    for (oid, _), r in indexed.items():
        _require(r['variant'] == 'B' and r['run_status'] == 'completed' and r['error'] is None and
                 audit.archive_fingerprint(units[oid], r, cfg, impl) == r['job_fingerprint'] and
                 r['samples'][0][1] == units[oid]['state'][-6:], 'Архивный B: fingerprint/начальное состояние')
        last, event = r['last_valid_time'], r['event']
        kinds = {'host_contact': 'reference_contact', 'other_contact': 'reference_contact',
                 'operational_escape': 'operational_escape'}
        valid_outcome = ((r['outcome'] == 'survived' and event is None and last == r['years'] * YEAR) or
                         (r['outcome'] in kinds and event is not None and
                          event.get('event') == kinds[r['outcome']] and event.get('time') == last))
        _require(math.isfinite(last) and 0 <= last <= r['years'] * YEAR and valid_outcome and
                 _contact_body_valid(r['outcome'], event, units[oid]) and
                 r['unresolved_reason'] is None and not r.get('stopped'), 'Архивный B: исход/время/событие')
    return units, indexed


def load(root, audit_path, expected_audit_sha256):
    """Проверить закреплённый снимок, вернуть cases для guard; без записи и классификации.

    expected_audit_sha256 должен быть получен независимо от переданного файла.
    Нельзя подавать незакреплённый отчёт или незаконченные серии. Оригинальные
    uncertain исходы сохраняются как есть; им не присваивается физическая метка.
    """
    data = Path(audit_path).read_bytes()
    _require(isinstance(expected_audit_sha256, str) and len(expected_audit_sha256) == 64 and
             _hash(data) == expected_audit_sha256, 'Внешний SHA audit.json не совпадает')
    baseline = _json(data)
    _require(baseline['schema'] == SCHEMA and baseline['integrity_passed'] is True and
             baseline['failures'] == [] and baseline['source_analysis'] == audit.ANALYSIS,
             'Аудит не принят или имеет неверную схему')
    _require({r['block']: r['run'] for r in baseline['runs']} == RUNS and len(baseline['runs']) == 3,
             'Неверный состав блоков в аудите')
    snapshot = capture(root, baseline['inventory'])
    cfg = yaml.safe_load(_get(snapshot, audit.CONFIG).decode('utf-8'))
    cases_doc, analysis = _json(_get(snapshot, audit.CASES)), _json(_get(snapshot, audit.ANALYSIS))
    _require(cfg['schema'] == 'w2-phase3-convergence-v1' and cases_doc['schema'] == 'w2-phase3-cases-v1',
             'Схема постановки')
    _require(_hash(_get(snapshot, audit.CONFIG)) == cases_doc['config_sha256'] == analysis['config_sha256'] and
             _hash(_get(snapshot, audit.CASES)) == analysis['cases_sha256'], 'Нарушен freeze')
    _require(_hash(_get(snapshot, 'src/submoon_research/diagnostics/convergence.py')) ==
             cases_doc['convergence_sha256'], 'Изменились frozen правила')
    b_units, b_rows = _archive(snapshot, cfg)
    manifests = {r['block']: r for r in analysis['runs']}
    _require(len(manifests) == len(analysis['runs']) == 3 and
             {b: manifests[b]['run'] for b in manifests} == RUNS, 'Состав анализа')
    case_list = cases_doc['cases']
    _require(len({c['orbit_id'] for c in case_list}) == len(case_list), 'Повтор случая')
    units_all, rows_all, dense = {}, [], {}
    for block, run in RUNS.items():
        mb = _get(snapshot, run + '/manifest.json')
        _require(_hash(mb) == manifests[block]['manifest_sha256'], 'SHA manifest')
        m = _json(mb)
        ub = _get(snapshot, run + '/units.json')
        _require(m['block'] == block and _hash(ub) == m['units_file_sha256'] and
                 m['cases_sha256'] == analysis['cases_sha256'] and
                 m['config_sha256'] == analysis['config_sha256'] and
                 m['implementations']['ias15'] == cfg['expected_b_implementation'], 'Контракт run')
        units = _units(ub)
        for oid, unit in units.items():
            _require(oid not in units_all or audit.canonical(unit) == audit.canonical(units_all[oid]),
                     'Различные units между блоками')
            units_all[oid] = unit
        rows = _jsonl(_get(snapshot, run + '/jobs.jsonl'))
        _validate_plan(m, cfg, case_list, units, rows)
        for row in rows:
            samples = _sample(snapshot, row, run, units[row['orbit_id']])
            if row['perturbation'] is None:
                dense[row['job_fingerprint']] = samples
        rows_all.extend(rows)
    _require(len(rows_all) == analysis['planned_jobs'] == analysis['rows'] == 439, 'Знаменатель заданий')
    output = []
    for c in case_list:
        oid, unit = c['orbit_id'], units_all[c['orbit_id']]
        mine = [r for r in rows_all if r['orbit_id'] == oid]
        ladders = {r['code']: r for r in mine if r['perturbation'] is None}
        _require(set(ladders) == set(c['codes']), 'Отсутствует ступень случая')
        pert = [r for r in mine if r['perturbation'] is not None]
        b = b_rows.get((oid, float(cfg['horizon_years'])))
        if b is not None:
            _require(oid in b_units and audit.canonical(b_units[oid]) == audit.canonical(unit),
                     'Архивный B: старт/физика отличаются от анализируемой орбиты')
            _require(all(r['years'] == b['years'] for r in ladders.values()),
                     'Архивный B: горизонт не совпадает')
        p0 = float(unit['period_seconds'])
        case = dict(rows=ladders, dense={k: dense[r['job_fingerprint']] for k, r in ladders.items()},
                    ladders=cfg['ladders'], a0=float(unit['a_km']), n0=2*math.pi/p0, p0=p0,
                    high_e=c['high_e'], perturb_family=c.get('perturb_family'), perturb_rows=pert,
                    scales=[float(s) for s in cfg['perturbation']['scales']])
        output.append(dict(orbit_id=oid, role=c['role'], case=case, archive_b=b))
    return dict(schema='w2-phase3-v2-input-v1', audit_sha256=expected_audit_sha256,
                inventory_sha256=audit.digest(baseline['inventory']), cases=output,
                thresholds=cfg['thresholds'], directions=cfg['perturbation']['directions'],
                runs=RUNS.copy(), rows=len(rows_all),
                limitations='Ретроспективный снимок; код анализатора и научная трактовка отдельно не допущены.')
