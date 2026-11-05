"""Read-only аудит W2-T064. Вывод только в новый отдельный каталог; интеграций нет."""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from submoon_research.diagnostics.phase3_audit import audit, sha  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    out = Path(args.output).resolve()
    # Не допустить записи доказательств в завершённые runs, даже новым подкаталогом.
    if out.exists() or ROOT / 'runs' == out or ROOT / 'runs' in out.parents:
        raise SystemExit('Нужен новый каталог вне runs/')
    result = audit(ROOT)
    result['utc'] = datetime.now(timezone.utc).isoformat()
    result['environment'] = dict(python=platform.python_version(), platform=platform.platform())
    result['audit_code_sha256'] = {p: sha(ROOT / p) for p in (
        'scripts/audit_phase3_results.py', 'src/submoon_research/diagnostics/phase3_audit.py')}
    out.mkdir(parents=True, exist_ok=False)
    (out / 'audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('integrity_passed', 'checks', 'failures', 'runs', 'previous_p1')},
                     ensure_ascii=False, indent=2))
    return 0 if result['integrity_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
