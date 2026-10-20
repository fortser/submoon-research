"""OPT-05: ни failed, ни обязательный skip не могут дать общий зелёный итог."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('check_w2', ROOT/'scripts/check_w2_stabilization.py')
assert spec is not None and spec.loader is not None
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_failure_traceback_and_aggregate_exit(tmp_path):
    failed = runner.run_step('bad', [sys.executable, '-c', 'raise RuntimeError("injected")'], tmp_path)
    good = runner.run_step('good', [sys.executable, '-c', 'print("ok")'], tmp_path)
    assert failed['exit_code'] != 0 and good['exit_code'] == 0
    assert 'Traceback' in (tmp_path/'bad.log').read_text(encoding='utf-8')
    assert runner.finish(tmp_path, [failed, good]) == 1
    assert json.loads((tmp_path/'checks.json').read_text(encoding='utf-8'))['status'] == 'failed_or_incomplete'


def test_required_skip_and_xml_summary(tmp_path):
    xml = tmp_path/'tests.xml'
    xml.write_text('<testsuites><testsuite><testcase name="ok" time="0.1" />'
                   '<testcase name="no_backend"><skipped message="missing backend"/></testcase>'
                   '</testsuite></testsuites>', encoding='utf-8')
    r = runner.run_step('skip', [sys.executable, '-c', 'pass'], tmp_path, junit=xml, require_no_skips=True)
    assert r['status'] == 'incomplete'
    assert r['pytest']['counts'] == dict(passed=1, failure=0, error=0, skipped=1)
    assert runner.finish(tmp_path, [r]) == 1


def test_timeout_not_success(tmp_path):
    r = runner.run_step('timeout', [sys.executable, '-c', 'import time; time.sleep(5)'], tmp_path, timeout=0.1)
    assert r['status'] == 'failed' and r['exit_code'] is None
    assert 'TimeoutExpired' in (tmp_path/'timeout.log').read_text(encoding='utf-8')
