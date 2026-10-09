"""W2-T008: последовательный запуск проверок и замера с общим журналом.

Вызывается из run_W2-T008_checks.bat. Каждая команда выполняется Python из
.venv; вывод показывается на экране и одновременно пишется в лог
scratch/claude/W2-T008_checks_<UTC>.log. Полный замер запускается только если
короткий пробный замер завершился успешно. В конце печатается сводка кодов.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
SUITE = sys.argv[1] if len(sys.argv) > 1 else 'lookup'
LOG = ROOT/'scratch'/'claude'/f'W2-T008_checks_{SUITE}_{STAMP}.log'

SUITES = {
    # Пакет 1: поиск сегмента кеша (W2-C019, W2-R004).
    'lookup': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_massive_cache_lookup.py',
                    'tests/physics/test_w2_engines.py']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/bench_cache_lookup.py', '--limit', '2', '--periods', '5',
                         '--repeats', '1', '--output', f'scratch/claude/W2-T008_bench_quick_{STAMP}.json']),
        ('bench_full', [PY, 'scripts/bench_cache_lookup.py', '--periods', '20', '--repeats', '3',
                        '--output', f'scratch/claude/W2-T008_bench_full_{STAMP}.json']),
    ],
    # Пакет 2: A против исправленного C, разбивка времени, масштабирование.
    'ac': [
        ('pytest', [PY, '-m', 'pytest', 'tests']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/bench_engines_ac.py', '--limit', '2', '--periods', '2',
                         '--repeats', '1', '--output', f'scratch/claude/W2-T008_ac_quick_{STAMP}.json']),
        ('bench_full', [PY, 'scripts/bench_engines_ac.py', '--periods', '20', '--repeats', '3',
                        '--output', f'scratch/claude/W2-T008_ac_single_{STAMP}.json']),
        ('bench_scaling', [PY, 'scripts/bench_engines_ac.py', '--scaling', '--scaling-periods', '10',
                           '--output', f'scratch/claude/W2-T008_ac_scaling_{STAMP}.json']),
    ],
    # Пакет 3 (этап D): общая подготовка событий шага; до/после и эквивалентность.
    'events': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_events_shared_samples.py',
                    'tests/unit/test_massive_cache_lookup.py', 'tests/physics']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('l1_timing', [PY, '-m', 'pytest', 'tests/integration/test_l1_acceptance.py',
                       '-k', 'canonical_control', '--durations=3']),
        ('bench_quick', [PY, 'scripts/bench_engine_versions.py', '--limit', '2', '--periods', '2',
                         '--repeats', '1', '--skip-c',
                         '--output', f'scratch/claude/W2-T008_versions_quick_{STAMP}.json']),
        ('bench_full', [PY, 'scripts/bench_engine_versions.py', '--periods', '20', '--repeats', '3',
                        '--output', f'scratch/claude/W2-T008_versions_full_{STAMP}.json']),
    ],
    # Пакет 4 (этап F): прототип скомпилированного DOP853 против scipy-пути A.
    'compiled': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_compiled_dop853.py',
                    'tests/unit/test_events_shared_samples.py', 'tests/physics']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/bench_compiled_a.py', '--limit', '2', '--periods', '2',
                         '--repeats', '1', '--output', f'scratch/claude/W2-T008_compiled_quick_{STAMP}.json']),
        ('bench_full', [PY, 'scripts/bench_compiled_a.py', '--periods', '20', '--repeats', '3',
                        '--output', f'scratch/claude/W2-T008_compiled_full_{STAMP}.json']),
        ('bench_long', [PY, 'scripts/bench_compiled_a.py', '--periods', '200', '--repeats', '1',
                        '--output', f'scratch/claude/W2-T008_compiled_long_{STAMP}.json']),
    ],
    # Пакет 5: масштабирование прототипа и scipy-пути A по процессам.
    'scaling': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_compiled_dop853.py']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/bench_compiled_scaling.py', '--levels', '1', '2',
                         '--replicas', '1', '--compiled-periods', '50', '--scipy-periods', '2',
                         '--output', f'scratch/claude/W2-T008_scaling_quick_{STAMP}.json']),
        ('bench_scaling', [PY, 'scripts/bench_compiled_scaling.py',
                           '--output', f'scratch/claude/W2-T008_scaling_full_{STAMP}.json']),
    ],
    # W2-P004, ступень P1: эквивалентность прототипа и scipy-пути A на 48 случаях.
    'p1': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_compiled_dop853.py',
                    'tests/unit/test_admission_evaluators.py', 'tests/unit/test_events_shared_samples.py',
                    'tests/physics']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/admission_p1_equivalence.py', '--limit', '4', '--periods', '2',
                         '--output', f'scratch/claude/W2-P004_P1_quick_{STAMP}.json']),
        ('bench_full', [PY, 'scripts/admission_p1_equivalence.py',
                        '--output', f'scratch/claude/W2-P004_P1_full_{STAMP}.json']),
    ],
    # W2-P004, ступень P2-1: D/T/B на ступенях 1–1000 лет (долгий прогон, продолжается после сбоя).
    'p2': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_compiled_dop853.py',
                    'tests/unit/test_admission_evaluators.py', 'tests/unit/test_native_ias15_guard.py']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/admission_p2_horizons.py', '--limit', '4', '--years', '0.01', '0.1',
                         '--b-max-years', '0.1', '--core-years', '0.01', '0.1', '--core-b-max', '0.1',
                         '--run-dir', f'scratch/claude/W2-P004_P2-1_quick_{STAMP}']),
        ('bench_full', [PY, 'scripts/admission_p2_horizons.py', '--run-dir', 'scratch/claude/W2-P004_P2-1_v4']),
    ],
    # W2-I015: страж полинома IAS15 (движок B) — тесты и диагностика на реальном случае отказа.
    'bguard': [
        ('pytest', [PY, '-m', 'pytest', 'tests/unit/test_native_ias15_guard.py',
                    'tests/unit/test_admission_evaluators.py']),
        ('ruff', [PY, '-m', 'ruff', 'check', 'src', 'scripts', 'tests']),
        ('bench_quick', [PY, 'scripts/diag_ias15_guard.py',
                         '--output', f'scratch/claude/W2-I015_diag_{STAMP}.json']),
        # Стенд v4 тем же коротким гейтом, что и на сервере (обязательный набор с B, расширение).
        ('stand_quick', [PY, 'scripts/admission_p2_horizons.py', '--limit', '4', '--years', '0.01', '0.03',
                         '0.1', '--b-max-years', '0.03', '--core-years', '0.01', '0.03', '--core-b-max',
                         '0.01', '--concurrent', '--workers', '4',
                         '--run-dir', f'scratch/claude/W2-P004_P2-1_v4_quick_{STAMP}']),
    ],
}
GATED = ('bench_full', 'bench_scaling', 'bench_long')  # запускаются, только если bench_quick прошёл


def main():
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except AttributeError:
        pass
    LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    results = []
    with LOG.open('w', encoding='utf-8', newline='\n') as log:
        def out(line):
            print(line, flush=True)
            log.write(line+'\n')
            log.flush()

        out(f'W2-T008 checks [{SUITE}] {STAMP}; python {PY}')
        for name, cmd in SUITES[SUITE]:
            if name in GATED and dict(results).get('bench_quick') != 0:
                out(f'\n=== {name}: ПРОПУЩЕН (пробный замер не прошёл) ===')
                results.append((name, None))
                continue
            out(f'\n=== {name}: {" ".join(cmd[1:])} ===')
            started = time.perf_counter()
            proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
            for raw in proc.stdout:
                out(raw.decode('utf-8', errors='replace').rstrip('\r\n'))
            code = proc.wait()
            out(f'=== {name}: код {code}, {time.perf_counter()-started:.1f} с ===')
            results.append((name, code))
        out('\nСВОДКА:')
        for name, code in results:
            out(f'  {name:12} {"пропущен" if code is None else ("OK" if code == 0 else f"ОШИБКА (код {code})")}')
        out(f'Лог: {LOG.relative_to(ROOT)}')
    return 0 if all(code == 0 for _, code in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
