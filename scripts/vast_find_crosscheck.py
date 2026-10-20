#!/usr/bin/env python3
"""Перекрёстная проверка полноты vast_find (W2-T017): всё из узкого профиля должно быть в широком.

Сравнивает два latest.json (по умолчанию p2_2a и all из data/interim/vast_find): предложения
и машины узкого профиля, которых нет в широком. Только чтение файлов, без сети.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--narrow', type=Path, default=ROOT/'data/interim/vast_find/p2_2a/latest.json')
    parser.add_argument('--wide', type=Path, default=ROOT/'data/interim/vast_find/all/latest.json')
    args = parser.parse_args(argv)
    narrow = json.loads(args.narrow.read_text(encoding='utf-8'))
    wide = json.loads(args.wide.read_text(encoding='utf-8'))
    offers = {r['offer_id'] for r in wide['rows']}
    machines = {r['machine_id'] for r in wide['rows']}
    missing = [r for r in narrow['rows'] if r['offer_id'] not in offers]
    lost = [r for r in missing if r['machine_id'] not in machines]
    print(f"узкий {narrow['profile']['name']} {narrow['utc']}: {len(narrow['rows'])}; "
          f"широкий {wide['profile']['name']} {wide['utc']}: {len(wide['rows'])}")
    print(f"нет в широком: предложений {len(missing)}, из них машин, которых там нет вовсе: {len(lost)}")
    for r in lost[:20]:
        print(f"  {r['offer_id']} машина {r['machine_id']} {r['cpu_key']} {r['vcpu']} vCPU ${r['usd_per_hour']:.3f}/ч")
    return 0 if not lost else 1


if __name__ == '__main__':
    raise SystemExit(main())
