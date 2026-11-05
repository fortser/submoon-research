"""Регрессии Windows lookup без запуска WSL; реальные syntax-тесты не заменены mock."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

CASES = [
    ('test_vast_p2_2a.py', 'test_p2_2a_script_order_and_commands', 1),
    ('test_vast_p2_b144.py', 'test_b144_script_order_and_pins', 1),
    ('test_vast_p2_rerun.py', 'test_start_script_pcores_mode_pins_stand_and_fails_without_pcores', 2),
]


def load_case(filename):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location('bash_regression_' + path.stem, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('filename,function,count', CASES)
@pytest.mark.parametrize('outcome', ['success', 'nonzero', 'timeout', 'missing_executable'])
def test_syntax_call_uses_resolved_path_and_does_not_hide_failures(monkeypatch, tmp_path,
                                                               filename, function, count, outcome):
    module = load_case(filename)
    resolved = str(tmp_path / 'Git with spaces' / 'bin' / 'bash.exe')
    original_which = module.shutil.which
    monkeypatch.setattr(module.shutil, 'which',
                        lambda name, *a, **kw: resolved if name == 'bash' else original_which(name, *a, **kw))
    calls = []

    def fake_run(argv, **kwargs):
        # При возврате bare 'bash' тест падает ДО запуска процесса.
        assert argv == [resolved, '-n']
        assert kwargs['timeout'] == 10 and kwargs['check'] is False
        assert not kwargs.get('shell', False)
        assert isinstance(kwargs['input'], bytes) and kwargs['input'].startswith(b'#!/bin/bash')
        calls.append(argv)
        if outcome == 'timeout':
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        if outcome == 'missing_executable':
            raise FileNotFoundError(resolved)
        return subprocess.CompletedProcess(argv, 0 if outcome == 'success' else 2)

    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    check = getattr(module, function)
    if outcome == 'success':
        check()
        assert len(calls) == count  # запрещает молчаливый обход syntax-проверки
    else:
        expected = {'nonzero': AssertionError, 'timeout': subprocess.TimeoutExpired,
                    'missing_executable': FileNotFoundError}[outcome]
        with pytest.raises(expected):
            check()
        assert len(calls) == 1
