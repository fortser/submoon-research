"""Структурный допуск RunContext; не проверка физики или байтов файлов.

После этой проверки consumer обязан проверить SHA сохранённых копий и свои
научные ворота. Legacy 0.2 не доказывает полноту начального набора. Контракт
не предназначен для диагностического просмотра pending/failed запусков или
специализированных манифестов PH3/B144.
"""
import re


def _canonical_code_path(value):
    return (isinstance(value, str) and bool(value)
            and not any(char in value for char in ('\\', ':', '\x00'))
            and all(part not in ('', '.', '..') for part in value.split('/')))


def validate_run_manifest_for_admission(manifest):
    """Отклонить неизвестную версию и несогласованный snapshot до допуска.

    Дополнительные code_sha256 после save_code допустимы; начальный список
    обязан быть подмножеством итогового индекса. Файлы здесь не открываются:
    freeze проверяет их прежним InputLedger, без второго чтения или кеша.
    """
    if not isinstance(manifest, dict):
        raise ValueError('Неизвестная версия RunContext: ожидается объект manifest')
    version = manifest.get('schema_version')
    if version == '0.2':
        if 'initial_code_snapshot' in manifest:
            raise ValueError('RunContext 0.2 не допускает initial_code_snapshot формата 0.3')
        return
    if version != '0.3':
        raise ValueError(f'Неизвестная версия RunContext: {version!r}')

    snapshot = manifest.get('initial_code_snapshot')
    if not isinstance(snapshot, dict) or snapshot.get('state') != 'complete':
        raise ValueError('RunContext snapshot 0.3: для допуска нужен complete')
    paths = snapshot.get('expected_paths')
    if not isinstance(paths, list) or not paths or not all(_canonical_code_path(p) for p in paths):
        raise ValueError('RunContext snapshot 0.3: неверные expected_paths')
    if len(set(paths)) != len(paths):
        raise ValueError('RunContext snapshot 0.3: повторяющиеся expected_paths')
    count = snapshot.get('published_count')
    if type(count) is not int or count != len(paths):
        raise ValueError('RunContext snapshot 0.3: несогласованный published_count')
    code = manifest.get('code_sha256')
    if not isinstance(code, dict) or not code:
        raise ValueError('RunContext snapshot 0.3: пустой или неверный code_sha256')
    for path, digest in code.items():
        if (not _canonical_code_path(path) or not isinstance(digest, str)
                or re.fullmatch(r'[0-9a-f]{64}', digest) is None):
            raise ValueError('RunContext snapshot 0.3: неверный путь или SHA-256 в code_sha256')
    if not set(paths).issubset(code):
        raise ValueError('RunContext snapshot 0.3: ожидаемые файлы отсутствуют в code_sha256')
