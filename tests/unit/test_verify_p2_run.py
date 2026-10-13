"""W2-I020: строгий приёмщик run P2 отвергает неполный или подменённый набор (fail-closed)."""
import importlib.util
import json
import math
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT/'runs'/'W2-P2-1-55404980'
SEED = ROOT/'results'/'evidence'/'W2_P2-1_remote_55404980'/'seed_info.json'
YEAR = 365.25*86400.0


def load():
    spec = importlib.util.spec_from_file_location('verify_p2_run', ROOT/'scripts'/'verify_p2_run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(**over):
    base = dict(orbit_id='a', years=1.0, variant='D', run_status='completed', error=None, stopped=False,
                last_valid_time=YEAR, samples=[[0.0, [0.0]*6], [YEAR, [1.0]*6]], final_probe=[1.0]*6,
                event=None, outcome='survived', unresolved_reason=None, prototype_version=5,
                numerical_domain=dict(high_e_phase=None, high_e_stop_time=None))
    base.update(over)
    return base


def test_composition_rejects_missing_duplicate_and_extra():
    v = load()
    expected = v.expected_keys(['a', 'b'], [1.0, 10.0], 10.0)
    rows = [row(orbit_id=c, years=y, variant=x) for c, y, x in sorted(expected)]
    assert v.check_composition(rows, expected) == []
    assert v.check_composition(rows[1:], expected)
    assert v.check_composition(rows + rows[:1], expected)
    assert v.check_composition(rows + [row(orbit_id='z')], expected)


def test_row_checks():
    v = load()
    assert v.check_row(row()) == []
    assert v.check_row(row(last_valid_time=math.nan))
    assert v.check_row(row(run_status='partial'))  # без текста ошибки
    assert v.check_row(row(run_status='failed', error='x', outcome='unresolved', unresolved_reason='computational',
                           last_valid_time=0.5*YEAR, samples=[[0.0, [0.0]*6]])) == []
    assert v.check_row(row(run_status='completed', error='x'))
    assert v.check_row(row(last_valid_time=0.5*YEAR, samples=[[0.0, [0.0]*6]]))  # без события до горизонта
    assert v.check_row(row(outcome='unresolved'))  # v5: unresolved без причины
    assert v.check_row(row(event=dict(event='reference_contact', time=YEAR, body=0), outcome='survived'))
    stop = 0.4*YEAR
    prolonged = row(last_valid_time=stop, samples=[[0.0, [0.0]*6]], outcome='unresolved',
                    unresolved_reason='high_e_prolonged',
                    numerical_domain=dict(high_e_phase='prolonged', high_e_stop_time=stop))
    assert v.check_row(prolonged) == []
    assert v.check_row(dict(prolonged, numerical_domain=dict(high_e_phase='terminal', high_e_stop_time=stop)))


def args_for(run, **over):
    argv = ['--run', str(run), '--years', '1', '10', '100', '1000', '10000', '--b-max-years', '1000',
            '--seed-info', str(SEED), '--expect-passed']
    for name, value in over.items():
        argv += [name, *value] if isinstance(value, list) else [name, value]
    return argv


@pytest.fixture
def run_copy(tmp_path):
    if not (RUN/'jobs.jsonl').exists() or not SEED.exists():
        pytest.skip('нет локальных данных run W2-P2-1-55404980')
    target = tmp_path/'run'
    target.mkdir()
    for name in ['jobs.jsonl', 'summary.json'] + [f'level_{y}y.json' for y in (1, 10, 100, 1000, 10000)]:
        shutil.copy(RUN/name, target/name)
    return target


def mutate_jobs(run, change):
    rows = [json.loads(x) for x in (run/'jobs.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    (run/'jobs.jsonl').write_text('\n'.join(json.dumps(r) for r in change(rows)) + '\n', encoding='utf-8')


def test_real_run_accepted_and_mutations_rejected(run_copy):
    v = load()
    assert v.main(args_for(run_copy)) == 0
    # 1) удалена целая ступень и её следы в summary/level — прежний аудитор это принимал
    broken = run_copy.parent/'no10000'
    shutil.copytree(run_copy, broken)
    mutate_jobs(broken, lambda rows: [r for r in rows if r['years'] != 10000.0])
    summary = json.loads((broken/'summary.json').read_text(encoding='utf-8'))
    summary['extension_levels'] = [s for s in summary['extension_levels'] if s['years'] != 10000.0]
    (broken/'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    (broken/'level_10000y.json').unlink()
    assert v.main(args_for(broken)) == 1
    # 2) дубль строки
    dup = run_copy.parent/'dup'
    shutil.copytree(run_copy, dup)
    mutate_jobs(dup, lambda rows: rows + rows[:1])
    assert v.main(args_for(dup)) == 1
    # 3) подменённый итог summary
    fake = run_copy.parent/'fake'
    shutil.copytree(run_copy, fake)
    summary = json.loads((fake/'summary.json').read_text(encoding='utf-8'))
    summary['extension_passed'] = False
    (fake/'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    assert v.main(args_for(fake)) == 1
    # 4) изменённая seed-строка
    seed = run_copy.parent/'seed'
    shutil.copytree(run_copy, seed)

    def touch_seed(rows):
        for r in rows:
            if r['years'] == 10000.0 and r['variant'] == 'D':
                r['steps'] += 1
                break
        return rows
    mutate_jobs(seed, touch_seed)
    assert v.main(args_for(seed)) == 1
