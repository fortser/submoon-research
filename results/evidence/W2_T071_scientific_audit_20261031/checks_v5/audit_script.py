"""CLI адресного научного аудита W2-T071; научная логика в diagnostics."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from submoon_research.diagnostics.physics_audit_v1 import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
