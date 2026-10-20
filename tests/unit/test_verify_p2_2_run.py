"""W2-P006 §7: стенд P2-2 на малом объёме и строгий приёмщик (fail-closed, отрицательные контроли)."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

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
    # Отпечаток B (admission_p2_horizons.b_fingerprint) читает версии rebound/reboundx;
    # в Windows .venv REBOUNDx нет (W2-D011), проверка идёт в WSL (W2-I022).
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
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
    # Отпечаток B (admission_p2_horizons.b_fingerprint) читает версии rebound/reboundx;
    # в Windows .venv REBOUNDx нет (W2-D011), проверка идёт в WSL (W2-I022).
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
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
    assert stand.main([*args, '--resume']) == 0
    report = verify(v, run, '--expect-complete')
    assert report['passed'], report['failures']
    assert report['checks']['jobs']['superseded_partial'] == 27


def test_stopped_b_accepted_as_decision_ready(tmp_path):
    """W2-D026: B остановлено сроком, D/T завершены — --expect-decision-ready принимает, --expect-complete нет."""
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    stand, v = load('admission_p2_2'), load('verify_p2_2_run')
    done_run, stopped_run = tmp_path/'done', tmp_path/'stopped'
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(done_run)]) == 0
    stopped_run.mkdir()
    stop = stopped_run/'STOP'
    stop.write_text('stop', encoding='utf-8')
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(stopped_run), '--stop-file', str(stop)]) == 1
    mixed = tmp_path/'mixed'
    mixed.mkdir()
    rows = ([r for r in lines(done_run/'jobs.jsonl') if r['variant'] != 'B']
            + [r for r in lines(stopped_run/'jobs.jsonl') if r['variant'] == 'B'])
    assert sum(1 for r in rows if r['variant'] == 'B') == 9
    write_lines(mixed/'jobs.jsonl', rows)
    analysis_dir = tmp_path/'mixed_analysis'
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(mixed), '--evaluate-only',
                       '--analysis-dir', str(analysis_dir)]) == 1
    # Материализуем синтетический fixture для приёмщика; стенд источник не менял.
    for name in ('analysis.json', 'summary.json'):
        shutil.copyfile(analysis_dir/name, mixed/name)
    summary = json.loads((mixed/'summary.json').read_text(encoding='utf-8'))
    summary.pop('input_contract_sha256')  # synthetic legacy fixture без units.json
    (mixed/'summary.json').write_text(json.dumps(summary), encoding='utf-8')
    analysis = json.loads((mixed/'analysis.json').read_text(encoding='utf-8'))
    level = analysis['horizons']['0.0003']
    assert level['dt_complete'] and not level['complete'] and level['b_unfinished'] == 9
    assert 'финальный горизонт D/T не завершён' not in ' '.join(analysis['decision']['reasons'])
    ready = verify(v, mixed, '--expect-decision-ready')
    assert ready['passed'], ready['failures']
    assert ready['checks']['analysis']['b_unfinished'] == 9 and ready['checks']['analysis']['dt_complete']
    assert not verify(v, mixed, '--expect-complete')['passed']
    # Остановленные D/T приёмщик с --expect-decision-ready не пропускает.
    assert not verify(v, stopped_run, '--expect-decision-ready')['passed']


def test_float_tolerance_only_for_floats():
    """W2-D036: допуск касается только float; целые, строки и состав — точно."""
    v = load('verify_p2_2_run')
    ref = dict(n=1152, verdict='inconclusive', cp=0.03975803115312412, cost=[17.895497290202314, 1.0])
    got = dict(n=1152, verdict='inconclusive', cp=0.039758031153124124, cost=[17.8954972902023, 1.0])
    assert v.differences(got, ref) != []                       # по умолчанию — точно
    assert v.differences(got, ref, rtol=1e-12) == []
    assert v.differences(dict(got, n=1151), ref, rtol=1e-12)    # целые не допускаются
    assert v.differences(dict(got, verdict='equivalent'), ref, rtol=1e-12)
    assert v.differences(dict(got, cp=0.0398), ref, rtol=1e-12)  # заметная разница — провал
    assert v.differences(dict(got, extra=1), ref, rtol=1e-12)
    assert v.floats_close(float('nan'), float('nan'), 0.0) and not v.floats_close(1.0, 2.0, 1e-9)
    report = v.verify(v.parse(['--run', 'nonexistent', *PARAMS, '--float-rtol', '1e-6']))
    assert not report['passed'] and 'float-rtol' in report['failures'][0]


def test_stand_refuses_foreign_or_accepted_run_dir(tmp_path):
    """W2-C067: стенд отказывает ДО записи при чужих строках, принятом run или других стартах."""
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    stand, v = load('admission_p2_2'), load('verify_p2_2_run')
    run = tmp_path/'run'
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(run)]) == 0
    assert (run/'units.json').exists()
    report = verify(v, run, '--expect-complete')
    assert report['passed'] and report['checks']['units_file'] == 'match'
    # принятый run: повторный разбор в тот же каталог запрещён, в отдельный — разрешён
    (run/'acceptance.json').write_text('{}', encoding='utf-8')
    before = {p.name: p.read_bytes() for p in run.iterdir()}
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(run), '--evaluate-only']) == 2
    assert {p.name: p.read_bytes() for p in run.iterdir()} == before
    other = tmp_path/'analysis'
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(run), '--evaluate-only',
                       '--analysis-dir', str(other)]) == 0
    assert (other/'analysis.json').exists() and {p.name: p.read_bytes() for p in run.iterdir()} == before
    # чужие строки (другой отпечаток): отказ, ничего не записано
    foreign = variant(tmp_path, run, 'foreign')
    (foreign/'acceptance.json').unlink()
    rows = lines(foreign/'jobs.jsonl')
    rows[0]['job_fingerprint'] = '0'*64
    write_lines(foreign/'jobs.jsonl', rows)
    before = {p.name: p.read_bytes() for p in foreign.iterdir()}
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(foreign)]) == 2
    assert {p.name: p.read_bytes() for p in foreign.iterdir()} == before
    # подменённый units.json: отказ стенда и провал приёмки
    tampered = variant(tmp_path, run, 'tampered')
    (tampered/'acceptance.json').unlink()
    doc = json.loads((tampered/'units.json').read_text(encoding='utf-8'))
    doc['units'][0]['state'][-1] += 1e-9
    (tampered/'units.json').write_text(json.dumps(doc), encoding='utf-8')
    assert stand.main([*PARAMS, '--workers', '0', '--run-dir', str(tampered)]) == 2
    assert not verify(v, tampered)['passed']
