"""Загрузка эталонных копий прежнего кода под отдельными именами модулей."""
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_reference():
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return (importlib.import_module('engine_compare_v0'),
            importlib.import_module('dense_contact_v0'),
            importlib.import_module('escape_v0'))
