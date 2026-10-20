"""Файловые контракты стендов P2; без импорта научных движков/REBOUNDx.

Legacy units_sha256 остаётся хешем стартов. contract_sha256 покрывает весь
документ (кроме самих хешей); SHA файла относится к буквальной сериализации.
Повреждённый JSONL, включая оборванный хвост, не восстанавливается молча.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
import math
import os
import tempfile
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'), default=float).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def units_digest(units):
    # Исторический алгоритм — нельзя менять идентичность завершённых run.
    payload = [(u['orbit_id'], u['seed'], u['state']) for u in units]
    return hashlib.sha256(json.dumps(payload, default=float).encode()).hexdigest()


def contract_digest(doc):
    return digest({k: v for k, v in doc.items() if k not in ('units_sha256', 'contract_sha256')})


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f'повтор ключа JSON: {key}')
        out[key] = value
    return out


def _constant(value):
    raise ValueError(f'неконечное число JSON: {value}')


def loads(text):
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)


def read_json(path):
    return loads(Path(path).read_text(encoding='utf-8'))


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('NaN/Inf в контракте')
    if isinstance(value, dict):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def _vector(value, size, name, positive=False):
    if not isinstance(value, list) or len(value) != size:
        raise ValueError(f'{name}: неверная размерность')
    if any(type(x) not in (int, float) or not math.isfinite(x)
           or (positive and x <= 0) for x in value):
        raise ValueError(f'{name}: недопустимые числа')


def validate_units_document(doc, expected):
    """Проверить структуру, фактические SHA и точное совпадение с ожидаемым входом.

    Legacy без полного хеша допускается только при полном сравнении с заново
    построенным ожидаемым документом. Это НЕ независимость от генератора (OPT-06).
    """
    if not isinstance(doc, dict):
        raise ValueError('units.json: нужен объект')
    _finite(doc)
    base = {'plan', 'stage', 'generator_numerics', 'params', 'units_sha256', 'units'}
    modern = {'contract', 'contract_sha256'}
    if set(doc) not in (base, base | modern):
        raise ValueError('units.json: неполный или неизвестный состав полей')
    items = doc['units']
    if not isinstance(items, list) or not items:
        raise ValueError('units.json: пустой/неверный набор единиц')
    ids, seeds = set(), set()
    for u in items:
        if not isinstance(u, dict):
            raise ValueError('units.json: единица не объект')
        for name in ('seed', 'cell', 'unit'):
            if type(u.get(name)) is not int or u[name] < 0:
                raise ValueError(f'units.json: неверный {name}')
        if not isinstance(u.get('orbit_id'), str) or not u['orbit_id']:
            raise ValueError('units.json: неверный orbit_id')
        if u['orbit_id'] in ids or u['seed'] in seeds:
            raise ValueError('units.json: повтор orbit_id или seed')
        ids.add(u['orbit_id'])
        seeds.add(u['seed'])
        gms = u.get('gms')
        if not isinstance(gms, list) or not gms:
            raise ValueError('units.json: нет массивных тел')
        _vector(gms, len(gms), 'gms', positive=True)
        _vector(u.get('radii'), len(gms), 'radii', positive=True)
        _vector(u.get('state'), 6*(len(gms)+1), 'state')
        for name in ('a_km', 'hill_km', 'escape_window', 'period_seconds', 'a_over_rh'):
            if type(u.get(name)) not in (int, float) or not u[name] > 0:
                raise ValueError(f'units.json: неверный {name}')
        figures = u.get('figures')
        if not isinstance(figures, list):
            raise ValueError('units.json: неверный figures')
        for f in figures:
            if (not isinstance(f, dict) or set(f) != {'index', 'j2', 'radius', 'pole'}
                    or type(f['index']) is not int or not 0 <= f['index'] < len(gms)
                    or type(f['radius']) not in (int, float) or f['radius'] <= 0
                    or type(f['j2']) not in (int, float)):
                raise ValueError('units.json: неверный контракт фигуры')
            _vector(f['pole'], 3, 'pole')
    if doc['units_sha256'] != units_digest(items):
        raise ValueError('units.json: заявленный SHA стартов не совпал с содержимым')
    if 'contract_sha256' in doc and doc['contract_sha256'] != contract_digest(doc):
        raise ValueError('units.json: SHA полного контракта не совпал с содержимым')
    # Canonical сохраняет типы (True != 1, 1 != 1.0), порядок единиц и все поля физики.
    fields = base | modern if 'contract' in doc else base
    if canonical({k: doc[k] for k in fields}) != canonical({k: expected[k] for k in fields}):
        raise ValueError('units.json: содержимое не совпало с ожидаемым входным контрактом')
    return doc


def read_job_rows(path, expected_fp):
    """Строгое продолжение: только известные задания, повтор только после partial."""
    path = Path(path)
    rows = {}
    if not path.exists():
        return rows
    raw = path.read_bytes()
    if raw and not raw.endswith(b'\n'):
        raise ValueError('jobs.jsonl: незавершённый хвост; требуется отдельное восстановление')
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        try:
            row = loads(line)
            if not isinstance(row, dict):
                raise ValueError('строка не объект')
            _finite(row)
            key = (row['orbit_id'], float(row['years']), row['variant'])
            if key not in expected_fp or row.get('job_fingerprint') != expected_fp[key]:
                raise ValueError('чужой ключ или отпечаток')
            if row.get('run_status') not in ('completed', 'failed', 'partial'):
                raise ValueError('неверный run_status')
            if key in rows and rows[key]['run_status'] != 'partial':
                raise ValueError('повтор поверх завершённого задания')
            rows[key] = row
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f'jobs.jsonl: строка {number}: {exc}') from exc
    return rows


def overlaps(a, b):
    a, b = Path(a).resolve(), Path(b).resolve()
    return a == b or a in b.parents or b in a.parents


def source_manifest(run):
    run = Path(run).resolve()
    return dict(path=str(run), files={p.relative_to(run).as_posix(): file_digest(p)
                                     for p in sorted(run.rglob('*')) if p.is_file()})


def preflight(run, output, *, evaluate, resume, rows, expected_fp, contract):
    """Не пишет ни одного файла. Run/анализ — непересекающиеся каталоги.

    Закрытие определяется не только acceptance: seal, complete, progress и
    фактический полный состав заданий. Legacy без manifest можно анализировать,
    но нельзя продолжать с неподтверждённым численным контрактом.
    """
    run = Path(run).resolve()
    output = Path(output).resolve() if output is not None else None
    manifest = run/'run_contract.json'
    if manifest.exists() and read_json(manifest) != contract:
        raise ValueError('run_contract.json не совпадает с текущей постановкой')
    if evaluate:
        if resume or not run.is_dir() or not (run/'jobs.jsonl').is_file():
            raise ValueError('анализ требует существующий run/jobs.jsonl и несовместим с --resume')
        if output is None or overlaps(run, output):
            raise ValueError('анализ требует отдельный непересекающийся --analysis-dir')
        if output.exists():
            raise ValueError('каталог анализа уже существует; нужен новый --analysis-dir')
        return
    if output is not None:
        raise ValueError('--analysis-dir разрешён только с --evaluate-only')
    protected = list(run.glob('acceptance*.json'))
    if (run/'sealed.json').exists() or protected:
        raise ValueError('run защищён (seal/отчёт приёмки); запись запрещена')
    for name in ('summary.json', 'progress.json'):
        path = run/name
        if path.exists():
            doc = read_json(path)
            if not isinstance(doc, dict):
                raise ValueError(f'{name}: неверный формат')
            if doc.get('complete') is True or (
                    doc.get('planned', 0) > 0 and doc.get('done') == doc['planned']
                    and not any(r['run_status'] == 'partial' for r in rows.values())):
                raise ValueError('run завершён; запись запрещена')
    if expected_fp and set(rows) == set(expected_fp) and all(
            r['run_status'] != 'partial' for r in rows.values()):
        raise ValueError('все задания терминальны; запись запрещена')
    if resume:
        if not manifest.is_file():
            raise ValueError('--resume: нет совместимого run_contract.json')
    elif any((run/name).exists() for name in ('jobs.jsonl', 'units.json', 'summary.json',
                                              'progress.json', 'run_contract.json', 'analysis.json')):
        raise ValueError('run уже существует; для незавершённого нужен явный --resume')


def atomic_json(path, value, *, replace=False):
    """Атомарная публикация; по умолчанию не заменяет существующий файл."""
    path = Path(path)
    data = json.dumps(value, ensure_ascii=False, indent=1, default=float, allow_nan=False)
    fd, tmp = tempfile.mkstemp(prefix='.'+path.name+'-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(tmp, path)
        else:
            # hard link публикует готовый inode и атомарно отклоняет существующий путь.
            os.link(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def exclusive_run(run, lock_root):
    """Сериализация писателей/анализа без файлов внутри защищаемого run.

    После аварийного убийства lock остаётся: удалять только после проверки процесса.
    """
    run = Path(run).resolve()
    lock_root = Path(lock_root)
    lock_root.mkdir(parents=True, exist_ok=True)
    lock = lock_root/(digest(os.path.normcase(str(run)))+'.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(f'run занят или оставлен аварийный lock: {lock}') from exc
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(dict(pid=os.getpid(), run=str(run)), stream)
        yield
    finally:
        lock.unlink()


def output_path(path, run):
    """Приёмщик не должен менять источник или предыдущий отчёт."""
    path, run = Path(path).resolve(), Path(run).resolve()
    if overlaps(path, run) or path.exists():
        raise ValueError('отчёт должен быть новым файлом вне исходного run')
    return path
