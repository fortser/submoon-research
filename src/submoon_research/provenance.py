"""Проверенные байты входов; пути и SHA относятся к фактически прочитанным данным."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml


def project_path(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError(f"Путь вне проекта: {name}")
    return path


class InputLedger:
    """Чтение, хеширование и снимок одной версии байтов без повторного открытия."""

    def __init__(self, root, folder=None, budget=None):
        self.root = Path(root).resolve()
        self.folder = folder
        self.budget = budget or (lambda: None)
        self.expected = {}
        self.used = {}
        self.cache = {}

    def bind(self, name, digest):
        name = project_path(self.root, name).relative_to(self.root).as_posix()
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Неверный SHA-256: {name}")
        if name in self.expected and self.expected[name] != digest:
            raise ValueError(f"Конфликт SHA-привязки: {name}")
        self.expected[name] = digest

    def bind_manifest(self, manifest):
        if not isinstance(manifest.get("files"), list) or not manifest["files"]:
            raise ValueError("Пустой манифест источников")
        for item in manifest["files"]:
            self.bind(item["path"], item["sha256"])

    def read(self, name, *, registered=False):
        self.budget()
        path = project_path(self.root, name)
        name = path.relative_to(self.root).as_posix()
        if name not in self.expected and not registered:
            raise ValueError(f"Вход не привязан к SHA-256: {name}")
        if name not in self.cache:
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if name in self.expected and digest != self.expected[name]:
                raise ValueError(f"SHA-256 входа изменился: {name}")
            if registered:
                self.bind(name, digest)
            self.cache[name] = data
            self.used[name] = digest
            if self.folder is not None and len(data) <= 2**20:
                target = self.folder / "inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            elif self.folder is not None:
                target = self.folder / 'inputs' / (name + '.ref.json')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(dict(path=name, sha256=digest, bytes=len(data),
                    disposition='immutable_original_required_for_restore')), encoding='utf-8')
            self.budget()
        return self.cache[name]

    def text(self, name, **kwargs):
        return self.read(name, **kwargs).decode("utf-8")

    def json(self, name, **kwargs):
        return json.loads(self.text(name, **kwargs), parse_constant=lambda value: self._bad(value))

    @staticmethod
    def _bad(value):
        raise ValueError(f"Неконечное JSON-число: {value}")

    def yaml(self, name, **kwargs):
        return yaml.safe_load(self.text(name, **kwargs))

    def verify_all(self):
        for name in self.expected:
            self.read(name)
