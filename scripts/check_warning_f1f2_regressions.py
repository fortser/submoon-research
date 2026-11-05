"""Негативные контроли регрессий F1/F2 в отдельных процессах.

Возвращает дефект только в памяти тестового процесса; файлы модулей не меняет.
Это самопроверка тестов, не независимый review и не научный запуск.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'src/submoon_research/diagnostics/warning_worker_demo_v2.py'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=False)
    before = hashlib.sha256(MODULE.read_bytes()).hexdigest()
    mutations = [
        ('F1', 'evaluate',
         "    if type(stdout) not in (bytes, str) or type(stderr) not in (bytes, str):\n"
         "        reasons.append('output_capture_unknown')\n", '',
         'missing_capture_is_unknown', 5),
        ('F2', 'verify_checkpoint', 'doc = parse_json(raw)', 'doc = read_json(path)',
         'verified_document_is_the_hashed_snapshot', 2),
    ]
    results = []
    for name, function, old, new, selection, count in mutations:
        junit = output / (name + '.xml')
        code = f'''
import inspect
import pytest
from submoon_research.diagnostics import warning_worker_demo_v2 as demo
source = inspect.getsource(getattr(demo, {function!r}))
assert source.count({old!r}) == 1
exec(compile(source.replace({old!r}, {new!r}), '<in-memory-{name}-mutation>', 'exec'), demo.__dict__)
raise SystemExit(pytest.main(['tests/unit/test_warning_worker_demo_v2.py', '-q',
                             '-k', {selection!r}, '--junitxml', {str(junit)!r}]))
'''
        proc = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
                              env=dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONIOENCODING='utf-8'),
                              capture_output=True, timeout=45, check=False)
        (output / (name + '.stdout.txt')).write_bytes(proc.stdout)
        (output / (name + '.stderr.txt')).write_bytes(proc.stderr)
        suite = ElementTree.parse(junit).getroot().find('testsuite')
        stats = {key: int(suite.attrib[key]) for key in ('tests', 'failures', 'errors')}
        passed = proc.returncode == 1 and stats == dict(tests=count, failures=count, errors=0)
        results.append(dict(defect=name, regression_detected=passed, returncode=proc.returncode, **stats))
    unchanged = hashlib.sha256(MODULE.read_bytes()).hexdigest() == before
    summary = dict(passed=unchanged and all(r['regression_detected'] for r in results),
                   module_unchanged=unchanged, module_sha256=before, controls=results)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return int(not summary['passed'])


if __name__ == '__main__':
    raise SystemExit(main())
