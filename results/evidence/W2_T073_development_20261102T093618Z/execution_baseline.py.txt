"""Единый локальный lifecycle с ранним manifest и ограниченными операциями."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import yaml

from submoon_research.provenance import InputLedger
from submoon_research.tracking import atomic_text, utc_now
from submoon_research.workflows.smoke import environment, sha256, write_json


class RunContext:
    def __init__(self, root, prefix, config=None, *, wall_seconds=120, output_mib=64):
        self.root = Path(root).resolve()
        self.run_id = prefix + '-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
        self.folder = self.root / 'runs' / self.run_id
        self.config = config or {}
        self.wall_seconds = wall_seconds
        self.output_bytes = output_mib * 2**20
        self.started_wall, self.started_cpu = time.perf_counter(), time.process_time()
        self.next_disk_check = 0.0
        self.manifest = dict(schema_version='0.2', run_id=self.run_id,
            experiment_id=self.config.get('experiment_id', prefix), scientific_id=None,
            started_utc=utc_now(), finished_utc=None, status='running',
            data_kind=self.config.get('data_kind', 'source_audit'),
            validation=dict(status='not_run'), inputs_sha256={}, code_sha256={},
            environment=environment(), artifacts_sha256={}, error=None,
            resources=dict(workers=1, threads=1, network_calls=0, paid_calls=0,
                wall_seconds=wall_seconds, output_mib=output_mib,
                memory_enforcement='not_OS_enforced_local_audits_only'))
        self.ledger = InputLedger(self.root, self.folder, self.check_budget)

    def __enter__(self):
        self.folder.mkdir(parents=True, exist_ok=False)
        for name in ('results','initial_conditions','logs','checkpoints'):
            (self.folder/name).mkdir()
        write_json(self.folder / 'manifest.json', self.manifest)
        write_json(self.folder / 'validation.json', dict(status='not_run'))
        try:
            self.resources = yaml.safe_load((self.root / 'configs/resources.yaml').read_text(encoding='utf-8'))
            if self.wall_seconds > self.resources['pilot_wall_seconds'] or self.output_bytes > self.resources['pilot_output_mib'] * 2**20:
                raise ValueError('Запрошенные лимиты выше глобального локального профиля')
            for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
                os.environ[name] = str(self.resources['threads_per_worker'])
            if shutil.disk_usage(self.root).free < self.resources['minimum_free_disk_gib'] * 2**30 + self.output_bytes:
                raise RuntimeError('Недостаточный резерв диска для планируемых выходов')
            self.ledger.read('configs/resources.yaml', registered=True)
            self.save_config(self.config)
            source=Path(__file__).resolve().parent
            for path in sorted(p for p in source.rglob('*') if p.suffix in ('.py','.json')):
                self.save_code(path)
            self.check_budget()
            return self
        except BaseException as exc:
            self.__exit__(type(exc), exc, exc.__traceback__)
            raise

    def save_code(self, path):
        if path.is_relative_to(self.root):
            name = path.relative_to(self.root).as_posix()
        else:
            name = 'src/submoon_research/'+path.relative_to(Path(__file__).resolve().parent).as_posix()
        data = path.read_bytes()
        target = self.folder / 'code' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.manifest['code_sha256'][name] = hashlib.sha256(data).hexdigest()
        write_json(self.folder / 'manifest.json', self.manifest)

    def save_config(self, config):
        self.config = config
        self.manifest['experiment_id'] = config.get('experiment_id', self.manifest['experiment_id'])
        self.manifest['data_kind'] = config.get('data_kind', self.manifest['data_kind'])
        atomic_text(self.folder / 'config.resolved.yaml', yaml.safe_dump(config, allow_unicode=True, sort_keys=False))

    def check_budget(self, *, force=False):
        now=time.perf_counter()
        if now - self.started_wall > self.wall_seconds:
            raise TimeoutError('Превышен wall budget')
        if force or now>=self.next_disk_check:
            size = sum(p.stat().st_size for p in self.folder.rglob('*') if p.is_file())
            if size > self.output_bytes:
                raise RuntimeError('Превышен дисковый бюджет run')
            self.next_disk_check=now+0.25

    def subprocess(self, arguments, **kwargs):
        self.check_budget()
        timeout = min(kwargs.pop('timeout', self.wall_seconds), self.wall_seconds - (time.perf_counter() - self.started_wall))
        result = subprocess.run(arguments, cwd=self.root, timeout=timeout, **kwargs)
        self.check_budget()
        return result

    def validate(self, checks, *, scope, **details):
        if not checks or any(type(v) is not bool for v in checks.values()):
            raise ValueError('Проверки должны быть непустым набором bool')
        validation = dict(status='passed' if all(checks.values()) else 'failed', checks=checks, scope=scope, **details)
        write_json(self.folder / 'validation.json', validation)
        self.manifest['validation'] = dict(status=validation['status'], scope=scope)
        return validation

    def __exit__(self, exc_type, exc, traceback):
        if exc is None:
            try:
                self.check_budget(force=True)
            except BaseException as error:
                self.__exit__(type(error), error, error.__traceback__)
                raise
        self.manifest['status'] = 'completed' if exc is None else 'cancelled' if isinstance(exc, KeyboardInterrupt) else 'failed'
        if exc is not None:
            self.manifest['error'] = f'{type(exc).__name__}: {exc}'
            self.manifest['validation']['status'] = 'failed' if self.manifest['validation']['status'] != 'not_run' else 'not_run'
            write_json(self.folder / 'validation.json', self.manifest['validation'])
        self.manifest.update(finished_utc=utc_now(), wall_seconds=time.perf_counter()-self.started_wall,
            cpu_seconds=time.process_time()-self.started_cpu, inputs_sha256=dict(self.ledger.used))
        identity = dict(config_sha256=sha256(self.folder / 'config.resolved.yaml') if (self.folder / 'config.resolved.yaml').is_file() else None, inputs=self.ledger.used,
            code=self.manifest['code_sha256'], environment=self.manifest['environment'])
        self.manifest['scientific_id'] = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
        for command, field in ((['git','rev-parse','HEAD'],'git_commit'), (['git','status','--porcelain'],'git_dirty')):
            try:
                result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=5)
                self.manifest[field] = (bool(result.stdout.strip()) if field == 'git_dirty' else result.stdout.strip()) if result.returncode == 0 else None
            except (OSError, subprocess.TimeoutExpired):
                self.manifest[field] = None
        write_json(self.folder / 'logs/execution.json', dict(status=self.manifest['status'], error=self.manifest['error']))
        self.manifest['artifacts_sha256'] = {p.relative_to(self.folder).as_posix():sha256(p)
            for p in self.folder.rglob('*') if p.is_file() and p != self.folder / 'manifest.json'}
        write_json(self.folder / 'manifest.json', self.manifest)
        return False
