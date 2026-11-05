"""Пропуск force budget только в памяти; один выбранный тест обязан упасть."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'src'))
import pytest  # noqa: E402
from submoon_research.execution import RunContext  # noqa: E402

original = RunContext.check_budget


def omit_forced_budget(self, *, force=False):
    if not force:
        return original(self, force=False)


RunContext.check_budget = omit_forced_budget
outcomes = []


class Observer:
    def pytest_runtest_logreport(self, report):
        outcomes.append((report.when, report.outcome))


rc = pytest.main([str(ROOT / 'tests/unit/test_provenance_batch.py')
                  + '::test_snapshot_output_budget_rejects_before_commit[True]', '-q', '--tb=short'],
                 plugins=[Observer()])
expected = rc == 1 and outcomes == [('setup', 'passed'), ('call', 'failed'), ('teardown', 'passed')]
print('BUDGET_MUTATION_DETECTED=', expected)
raise SystemExit(0 if expected else 1)
