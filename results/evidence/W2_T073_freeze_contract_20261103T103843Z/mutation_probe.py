"""Возврат пропуска guard только в памяти; ожидание — один провал теста."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
import pytest  # noqa: E402
import submoon_research.contracts.freeze as freeze  # noqa: E402

freeze.validate_run_manifest_for_admission = lambda manifest: None
outcomes = []


class Observer:
    def pytest_runtest_logreport(self, report):
        outcomes.append((report.when, report.outcome))


rc = pytest.main([
    str(ROOT / 'tests/unit/test_run_manifest_freeze.py') + '::test_missing_snapshot_rejected',
    '-q', '--tb=short',
], plugins=[Observer()])
expected = rc == 1 and outcomes == [('setup', 'passed'), ('call', 'failed'), ('teardown', 'passed')]
print('MUTATION_DETECTED=', expected)
raise SystemExit(0 if expected else 1)
