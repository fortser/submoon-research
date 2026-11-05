"""Воспроизведение F1 независимого обзора; только синтетический subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
from submoon_research.diagnostics.warning_worker_demo import evaluate  # noqa: E402


def main():
    with tempfile.TemporaryDirectory(prefix='w2-capture-review-') as tmp:
        directory = Path(tmp) / 'stderr_job'
        env = os.environ.copy()
        env['PYTHONPATH'] = str(ROOT / 'src')
        process = subprocess.run(
            [sys.executable, '-m', 'submoon_research.diagnostics.warning_worker_demo',
             '--directory', str(directory), '--contract', 'demo-fingerprint', '--mode', 'stderr'],
            env=env, timeout=30, check=False,
        )
        assert process.returncode == 0
        assert process.stdout is None and process.stderr is None
        cases = {}
        for name, stdout, stderr in [
            ('real_uncaptured_process', process.stdout, process.stderr),
            ('unknown_stderr', b'', None),
            ('unknown_stdout', None, b''),
        ]:
            result = evaluate(directory, 'demo-fingerprint', process.returncode, stdout, stderr)
            assert result['admissible']
            cases[name] = result
        control = evaluate(directory, 'demo-fingerprint', 0, b'', b'native diagnostic on stderr\n')
        assert not control['admissible']
        cases['known_output_control'] = control
        print(json.dumps({'scope': 'synthetic reproduction of missing capture defect',
                          'python': sys.version, 'results': cases}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
