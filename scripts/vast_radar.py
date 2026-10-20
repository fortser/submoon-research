#!/usr/bin/env python3
"""Read-only радар станций; --desktop для трея, --open для страницы, --once для опроса."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))

from submoon_research.compute.radar_app import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main())
