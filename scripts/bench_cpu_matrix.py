"""Тонкий CLI машинного бенчмарка W2-T006; научная логика в src."""
import os
from pathlib import Path
import sys

for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMBA_NUM_THREADS'):
    os.environ[name] = '1'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from submoon_research.workflows.w2_longbench import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
