"""W2-P006 §7: стенд P2-2 на малом объёме и строгий приёмщик (fail-closed, отрицательные контроли)."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
YEAR = 365.25*86400.0
PARAMS = ['--per-cell', '1', '--b-per-cell', '1', '--years', '0.0003', '--b-years', '0.0003']


def load(name):
    sys.path.insert(0, str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify(v, run, *extra):
    return v.verify(v.parse(['--run', str(run), *PARAMS, *extra]))


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def write_lines(path, rows):
    path.write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')


def variant(tmp_path, run, name):
    target = tmp_path/name
    shutil.copytree(run, target)
    return target


def test_stand_run_accepted_and_tampering_rejected(tmp_path):
    stand, v = load('admission_p2_2'), load('verify_p2_2_run')
    run = tmp_path/'run'
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(run)]) == 0
    rows = lines(run/'jobs.jsonl')
    assert len(rows) == 27 and {r['variant'] for r in rows} == {'D', 'T', 'B'}
    assert all(r['last_valid_time'] <= r['years']*YEAR for r in rows)  # W2-I021
    good = verify(v, run, '--expect-complete')
    assert good['passed'], good['failures']
    assert good['checks']['analysis']['decision'] == 'not_decided'  # сокращённый объём
    assert not verify(v, run, '--expect-full')['passed']

    bad = variant(tmp_path, run, 'missing')
    write_lines(bad/'jobs.jsonl', rows[1:])
    assert any('не хватает' in f for f in verify(v, bad)['failures'])

    bad = variant(tmp_path, run, 'repeat')
    write_lines(bad/'jobs.jsonl', rows + rows[:1])
    assert any('повтор' in f for f in verify(v, bad)['failures'])

    bad = variant(tmp_path, run, 'outcome')
    changed = [dict(r) for r in rows]
    changed[0]['outcome'] = 'host_contact' if changed[0]['outcome'] != 'host_contact' else 'survived'
    write_lines(bad/'jobs.jsonl', changed)
    assert not verify(v, bad)['passed']

    bad = variant(tmp_path, run, 'fingerprint')
    changed = [dict(r) for r in rows]
    changed[3]['job_fingerprint'] = '0'*64
    write_lines(bad/'jobs.jsonl', changed)
    assert any('отпечаток' in f for f in verify(v, bad)['failures'])

    bad = variant(tmp_path, run, 'seed')
    changed = [dict(r) for r in rows]
    changed[5]['seed'] += 1
    write_lines(bad/'jobs.jsonl', changed)
    assert any('seed' in f for f in verify(v, bad)['failures'])

    bad = variant(tmp_path, run, 'summary')
    summary = json.loads((bad/'summary.json').read_text(encoding='utf-8'))
    summary['params']['per_cell'] = 2
    (bad/'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    assert any('per_cell' in f for f in verify(v, bad)['failures'])

    bad = variant(tmp_path, run, 'analysis')
    analysis = json.loads((bad/'analysis.json').read_text(encoding='utf-8'))
    analysis['horizons']['0.0003']['pairs']['D-T']['levels']['pooled'][0]['discordant'] = 1
    (bad/'analysis.json').write_text(json.dumps(analysis), encoding='utf-8')
    assert any('analysis.json' in f for f in verify(v, bad)['failures'])

    # Другой объём задания: состав и summary не совпадают.
    other = v.verify(v.parse(['--run', str(run), '--per-cell', '2', '--b-per-cell', '1', '--years', '0.0003',
                              '--b-years', '0.0003']))
    assert not other['passed']


def test_stopped_run_resumes_and_superseded_rows_accepted(tmp_path):
    stand, v = load('admission_p2_2'), load('verify_p2_2_run')
    run = tmp_path/'run'
    run.mkdir()
    stop = run/'STOP'
    stop.write_text('stop', encoding='utf-8')
    args = [*PARAMS, '--workers', '0', '--run-dir', str(run), '--stop-file', str(stop)]
    assert stand.main(args) == 1
    partial = lines(run/'jobs.jsonl')
    assert len(partial) == 27 and all(r['run_status'] == 'partial' and r['stopped'] for r in partial)
    report = verify(v, run)
    assert report['passed'], report['failures']
    assert report['checks']['analysis']['complete'] is False
    assert not verify(v, run, '--expect-complete')['passed']
    stop.unlink()
    assert stand.main(args) == 0
    report = verify(v, run, '--expect-complete')
    assert report['passed'], report['failures']
    assert report['checks']['jobs']['superseded_partial'] == 27
