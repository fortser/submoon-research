#!/usr/bin/env python3
"""Локальный пакет P2-2a и явные команды стенда. НЕТ API-вызовов или аренды.

Не использовать deploy старого P2-1 для P2-2a. Этот файл только подготавливает
пакет и список команд для оператора; запуск после отдельного разрешения.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(output):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT/'scratch') or output.exists():
        raise ValueError('Нужен новый каталог внутри scratch/; прежний пакет не заменяется')
    spec = importlib.util.spec_from_file_location('p2_transport_prepare', ROOT/'scripts/vast_p2.py')
    if spec is None or spec.loader is None:
        raise RuntimeError('Не найден транспорт подготовки')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORK = output
    module.MANIFEST = output/'bundle_manifest.json'
    module.BUNDLE = output/'bundle.tar.gz'
    module.prepare()  # Только локальные файлы; approval/rent/deploy не вызываются.
    commands = dict(
        approved=False, rental_enabled=False, scenario='p2_2a', expected_jobs=9504,
        full=['.venv/bin/python', '-u', 'scripts/admission_p2_2.py', '--per-cell', '128',
              '--b-per-cell', '16', '--unit-start', '0', '--years', '1', '10', '100', '1000',
              '--b-years', '100', '1000', '--workers', '<BASELINE_WORKERS>',
              '--run-dir', 'runs/<NEW_RUN_ID>', '--deadline-utc', '<APPROVED_UTC_DEADLINE>',
              '--stop-file', '<RUN_SPECIFIC_STOP_FILE>'],
        accept=['.venv/bin/python', 'scripts/verify_p2_2_run.py', '--run', 'runs/<NEW_RUN_ID>',
                '--expect-full', '--expect-complete', '--output',
                'results/evidence/<NEW_RUN_ID>-acceptance.json'],
        baseline=['.venv/bin/python', '-u', 'scripts/admission_p2_2.py', '--per-cell', '4',
                  '--b-per-cell', '4', '--years', '0.1', '--b-years', '0.1',
                  '--workers', '<BASELINE_WORKERS>', '--run-dir', 'runs/<NEW_RUN_ID>-baseline',
                  '--deadline-utc', '<BASELINE_DEADLINE_MAX_10_MIN>',
                  '--stop-file', '<RUN_SPECIFIC_STOP_FILE>'],
        baseline_accept=['.venv/bin/python', 'scripts/verify_p2_2_run.py', '--run',
                         'runs/<NEW_RUN_ID>-baseline', '--per-cell', '4', '--b-per-cell', '4',
                         '--years', '0.1', '--b-years', '0.1', '--expect-complete'],
        output_files=['jobs.jsonl', 'analysis.json', 'summary.json', 'progress.json',
                      'units.json', 'run_contract.json', 'sealed.json', 'stand_stdout.log'],
        acceptance_output='results/evidence/<NEW_RUN_ID>-acceptance.json',
        prerequisites=['Отдельное явное согласие на конкретную новую аренду.',
                       'Проверенный оператор контроля срока/расходов, выгрузки и destroy.',
                       'Подтверждённые CPU affinity/cgroup, RAM, топология; один поток на физическое ядро.',
                       'Прогрев, короткий гейт, проверка REBOUNDx без skipped.',
                       'Замер скорости B и параллельной нагрузки; прогноз завершения до срока с резервом.',
                       'Изолированный новый удалённый каталог; SHA архива до распаковки.',
                       'Базовый прогон НЕ засчитывается как полный P2-2a и не меняет научных критериев.'])
    (output/'commands.json').write_text(json.dumps(commands, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    print('Подготовлено локально, аренда отключена:', prepare(args.output))


if __name__ == '__main__':
    main()
