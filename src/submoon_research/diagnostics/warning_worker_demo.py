"""Изолированный синтетический прототип worker/ledger; НЕ научный runner.

Только однопроцессная Python-задача; непустой stderr/stdout не анализируется,
а запрещает приёмку. Дочерние процессы внутри worker и C-level warning без
вывода не покрыты. Физический checkpoint здесь — синтетический JSON.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = 'W2-warning-worker-demo-v1'
MAX_EVENTS = 8
MAX_BYTES = 8192


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def sha(value):
    return hashlib.sha256(value).hexdigest()


def read_json(path):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError('duplicate key')
            out[key] = value
        return out
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


def append(path, record):
    with path.open('ab') as stream:
        stream.write(encode(record) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())


def write_atomic(path, record):
    temp = path.with_name(path.name + '.pending')
    with temp.open('xb') as stream:
        stream.write(encode(record))
        stream.flush()
        os.fsync(stream.fileno())
    if path.exists():
        raise FileExistsError(path)
    os.replace(temp, path)


def verify_checkpoint(path, expected_sha, contract):
    raw = path.read_bytes()
    if sha(raw) != expected_sha:
        raise ValueError('checkpoint hash mismatch')
    doc = read_json(path)
    if (set(doc) != {'schema', 'contract', 'physical_state', 'events', 'overflow', 'capture_complete', 'status'}
            or doc['schema'] != SCHEMA or doc['contract'] != contract
            or type(doc['overflow']) is not bool or doc['capture_complete'] is not True
            or not isinstance(doc['events'], list) or len(doc['events']) > MAX_EVENTS
            or not isinstance(doc['physical_state'], dict)):
        raise ValueError('invalid checkpoint')
    return doc


def worker(directory, contract, mode, parent=None, parent_sha=None):
    """Демо worker: создаёт собственный журнал, checkpoint и финальный receipt."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    journal = directory / 'warnings.jsonl'
    journal.touch(exist_ok=False)
    previous = verify_checkpoint(Path(parent), parent_sha, contract) if parent else None
    if previous and previous['status'] != 'partial':
        raise ValueError('cannot resume terminal checkpoint')
    events = previous['events'].copy() if previous else []
    overflow = previous['overflow'] if previous else False
    # Сохраняем предков в журнале новой попытки; каждая строка привязана к seq.
    for event in events:
        append(journal, event)

    def capture(message, category, filename, lineno, file=None, line=None):
        nonlocal overflow
        item = dict(sequence=len(events) + 1, utc=datetime.now(timezone.utc).isoformat(),
                    pid=os.getpid(), category=category.__name__, message=str(message),
                    phase='synthetic', model_time=None, filename=str(filename), line=int(lineno))
        if len(events) >= MAX_EVENTS or len(encode(item)) > MAX_BYTES:
            overflow = True
            return
        append(journal, item)
        events.append(item)

    with warnings.catch_warnings():
        warnings.simplefilter('always')
        warnings.showwarning = capture
        if mode in {'warning', 'partial_warning', 'crash_warning', 'wait_after_warning'}:
            warnings.warn('synthetic warning', RuntimeWarning)
        if mode == 'overflow':
            for index in range(MAX_EVENTS + 2):
                warnings.warn(f'warning {index}', RuntimeWarning)
        if mode == 'stderr':
            os.write(2, b'native diagnostic on stderr\n')
        if mode == 'wait_after_warning':
            time.sleep(30)
        if mode == 'crash_warning':
            os._exit(17)
        if mode == 'exception':
            raise RuntimeError('synthetic worker failure')
        checkpoint = dict(schema=SCHEMA, contract=contract, physical_state={'synthetic_time': 1.0},
                          events=events, overflow=overflow, capture_complete=True,
                          status='partial' if mode == 'partial_warning' else 'completed')
        if mode == 'crash_before_checkpoint':
            os._exit(18)
        write_atomic(directory / 'checkpoint.json', checkpoint)
        if mode == 'crash_after_checkpoint':
            os._exit(19)
        receipt = dict(schema=SCHEMA, contract=contract, status='partial' if mode == 'partial_warning' else 'completed',
                       journal_sha256=sha(journal.read_bytes()), checkpoint_sha256=sha((directory / 'checkpoint.json').read_bytes()),
                       warning_count=len(events), overflow=overflow, capture_complete=True)
        write_atomic(directory / 'receipt.json', receipt)


def evaluate(directory, contract, returncode, stdout, stderr):
    """Финализация по артефактам только после завершения worker; ошибки -> unresolved."""
    directory = Path(directory)
    reasons = []
    if returncode != 0:
        reasons.append('worker_failed')
    if stdout or stderr:
        reasons.append('unclassified_output')
    try:
        receipt = read_json(directory / 'receipt.json')
        journal = (directory / 'warnings.jsonl').read_bytes()
        if journal and not journal.endswith(b'\n'):
            raise ValueError('truncated journal')
        records = [json.loads(line) for line in journal.decode('utf-8').splitlines()]
        cp = verify_checkpoint(directory / 'checkpoint.json', receipt['checkpoint_sha256'], contract)
        if (set(receipt) != {'schema', 'contract', 'status', 'journal_sha256', 'checkpoint_sha256',
                             'warning_count', 'overflow', 'capture_complete'}
                or receipt['schema'] != SCHEMA or receipt['contract'] != contract
                or receipt['capture_complete'] is not True or receipt['status'] not in ('partial', 'completed')
                or type(receipt['overflow']) is not bool
                or receipt['journal_sha256'] != sha(journal) or records != cp['events']
                or receipt['warning_count'] != len(records) or receipt['overflow'] != cp['overflow']
                or receipt['status'] != cp['status']):
            raise ValueError('receipt mismatch')
        if receipt['status'] != 'completed':
            reasons.append('partial')
        if records:
            reasons.append('warnings_present')
        if receipt['overflow']:
            reasons.append('overflow')
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
        reasons.append('capture_incomplete')
    return dict(admissible=not reasons, outcome='unresolved' if reasons else 'preserve_synthetic_outcome', reasons=reasons)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--contract', required=True)
    parser.add_argument('--mode', choices=('clean', 'warning', 'partial_warning', 'overflow', 'stderr',
                                           'crash_warning', 'crash_before_checkpoint', 'crash_after_checkpoint',
                                           'wait_after_warning', 'exception'), required=True)
    parser.add_argument('--parent')
    parser.add_argument('--parent-sha')
    args = parser.parse_args()
    if (args.parent is None) != (args.parent_sha is None):
        parser.error('parent and parent-sha must be specified together')
    worker(args.directory, args.contract, args.mode, args.parent, args.parent_sha)


if __name__ == '__main__':
    main()
