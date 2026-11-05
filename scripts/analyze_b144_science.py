"""Reproduce the post-hoc scientific analysis without modifying completed runs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from submoon_research.diagnostics.b144_scientific import analyze  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Output exists; choose a new analysis version')
    result = analyze(ROOT)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps(dict(output=str(args.output), integrity=result['integrity'],
                         jobs=result['jobs'], discordant_unique_units=result['discordant_unique_units']),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
