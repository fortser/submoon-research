"""Ограниченный benchmark общего горизонта W1, без основного ансамбля."""
import json
import os
import sys
from pathlib import Path

for variable in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[variable] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from submoon_research.execution import RunContext  # noqa: E402
from submoon_research.workflows.w1_benchmark import benchmark  # noqa: E402


def main():
    root = Path(__file__).resolve().parents[1]
    with RunContext(root, 'W1-cost', wall_seconds=240, output_mib=64) as run:
        run.save_code(Path(__file__).resolve())
        summary = benchmark(run)
    print(json.dumps(dict(run_id=run.run_id, summary=summary)))


if __name__ == '__main__':
    main()
