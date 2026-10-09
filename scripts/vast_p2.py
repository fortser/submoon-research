"""W2-P004 / P2-1 (вариант В) на одной арендованной CPU-машине Vast.ai.

Запуск глобальным Python Windows с установленными vastai и paramiko (как vast_w2.py);
SSH-ключ ~/.ssh/vast_agent. Ключи API на сервер не копируются и в файлы не пишутся.
Платные действия (rent) выполняются только при scratch/p2_rental/approval.json с явным
согласием пользователя и в его пределах. Локального сторожа нет: компьютер пользователя
может быть выключен; удаление инстанса — действием finish (утром) или destroy.

Действия:
  preflight — CLI, SSH-ключ, paramiko, approval, баланс (без затрат)
  offers    — бесплатный анонимный поиск подходящих машин -> scratch/p2_rental/offers.json
  prepare   — пакет проекта с манифестом SHA -> scratch/p2_rental/bundle.tar.gz
  rent      — аренда ОДНОГО инстанса из offers.json в пределах approval (платно)
  wait      — ожидание состояния running (иначе удаление по таймауту)
  deploy    — загрузка пакета, сверка SHA, запуск удалённого сценария (nohup); срок стенда =
              создание + run_deadline_hours из approval (по умолчанию 13.5 ч)
  status    — состояние, оценка расходов, этап удалённого сценария, прогресс P2-1, хвост лога
  wrapup    — досрочное подведение итога: стоп-файл на сервере; задания останавливаются
              (partial), стенд пишет summary.json, этап становится done (обычно 1–3 мин)
  download  — упаковка результатов на сервере, выгрузка, сверка SHA, распаковка в runs/
  finish    — download + destroy + проверка удаления + итог расходов (только после done;
              --partial — выгрузить то, что есть, если стенд не завершился)
  destroy   — удаление без выгрузки (только по прямому указанию пользователя)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vast_w2 as vw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'scratch/p2_rental'
APPROVAL = WORK/'approval.json'
RENTAL = WORK/'rental.json'
BUNDLE = WORK/'bundle.tar.gz'
MANIFEST = WORK/'bundle_manifest.json'
REMOTE_ROOT = '/workspace/submoon-research'
BUNDLES = 'https://console.vast.ai/api/v0/bundles/'
LIVE = ('created', 'running', 'deployed')
STOP_FILE = '/workspace/p2_stop'
DEFAULT_RUN_DEADLINE_HOURS = 13.5


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    vw.save(path, value)


def approval():
    value = load(APPROVAL)
    required = ('approved', 'user_message', 'budget_usd', 'max_hourly_usd', 'max_hours',
                'min_effective_cpus', 'cpu_name_regex', 'max_rent_attempts')
    if not value or value.get('approved') is not True or any(k not in value for k in required):
        raise ValueError('Нет полного явного разрешения пользователя в '+str(APPROVAL))
    if float(value['max_hourly_usd'])*float(value['max_hours']) > float(value['budget_usd']):
        raise ValueError('Ставка x длительность превышает бюджет approval')
    return value


def preflight():
    plan = approval()
    key = Path.home()/'.ssh/vast_agent'
    checks = dict(cli=bool(vw.CLI), ssh_key=key.exists(), approval=True)
    try:
        import paramiko  # noqa: F401
        checks['paramiko'] = True
    except ImportError:
        checks['paramiko'] = False
    user = vw.cli('show', 'user')
    checks['credit_usd'] = user.get('credit') if isinstance(user, dict) else None
    checks['budget_usd'] = plan['budget_usd']
    checks['enough_credit'] = (checks['credit_usd'] is not None
                               and float(checks['credit_usd']) >= float(plan['budget_usd']))
    live = [r for r in [load(RENTAL)] if r and r.get('status') in LIVE]
    checks['existing_live_rental'] = bool(live)
    print(json.dumps(checks, ensure_ascii=False))
    if not all(checks[k] for k in ('cli', 'ssh_key', 'paramiko')) or live:
        raise SystemExit('preflight: не пройдено')


def search_offers():
    plan = approval()
    body = {'rentable': {'eq': True}, 'rented': {'eq': False}, 'type': 'on-demand',
            'allocated_storage': int(plan.get('disk_gb', 60)), 'order': [['dph_total', 'asc']],
            'limit': 1000, 'cpu_arch': {'eq': 'amd64'}, 'num_gpus': {'eq': 0},
            'disk_space': {'gte': int(plan.get('disk_gb', 60))},
            'cpu_cores_effective': {'gte': int(plan['min_effective_cpus'])},
            'dph_total': {'lte': float(plan['max_hourly_usd'])},
            'reliability': {'gte': float(plan.get('min_reliability', 0.97))}}
    request = urllib.request.Request(BUNDLES, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=120) as response:
        data = json.loads(response.read().decode('utf-8', 'replace'))
    raw = data.get('offers', []) if isinstance(data, dict) else []
    pattern = re.compile(plan['cpu_name_regex'], re.I)
    min_ram = float(plan.get('min_ram_gb', 64))*1000
    found = []
    for o in raw:
        clean = {k: o.get(k) for k in vw.SAFE_OFFER_FIELDS}
        if clean.get('resource_type') not in ('cpu', 'compute'):
            continue  # num_gpus=0 включает дисковые контракты
        if not pattern.search(clean.get('cpu_name') or ''):
            continue
        if float(clean.get('cpu_ram') or 0) < min_ram:
            continue
        clean['usd_per_effective_core_hour'] = float(clean['dph_total'])/max(1, int(clean['cpu_cores_effective']))
        found.append(clean)
    # Дешевле ядро-час — выше; при равенстве надёжнее машина.
    found.sort(key=lambda o: (o['usd_per_effective_core_hour'], -(o.get('reliability') or 0)))
    save(WORK/'offers.json', dict(utc=vw.now(), source='anonymous_bundles', filter=body,
                                  cpu_name_regex=plan['cpu_name_regex'], offers=found))
    for o in found[:15]:
        print(json.dumps({k: o.get(k) for k in ('id', 'machine_id', 'cpu_name', 'cpu_cores_effective',
              'cpu_ram', 'dph_total', 'reliability', 'geolocation', 'disk_space')}, ensure_ascii=False))
    print(f'подходящих предложений: {len(found)}; сохранено в {WORK/"offers.json"}')
    return found


def prepare():
    WORK.mkdir(parents=True, exist_ok=True)
    # Только то, что нужно гейтам и load_cases (модель, состояния, контрасты); крупные
    # data/raw и data/interim удалённому P2-1 не нужны.
    roots = ('src', 'scripts', 'tests', 'configs', 'data/processed', 'data/manifests',
             'tracking', 'references', 'docs')
    paths = [p for name in roots for p in (ROOT/name).rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and 'vast_market' not in p.parts
             and p.suffix not in ('.pyc', '.pem', '.key') and p.name != '.write.lock']
    # Входы load_cases (W2_three_engines_v1.yaml) и малые артефакты runs (контрасты и т.п.).
    for p in (ROOT/'runs').rglob('*'):
        if (p.is_file() and p.stat().st_size <= 2*2**20 and '__pycache__' not in p.parts
                and p.suffix not in ('.gz', '.zip', '.npz', '.parquet', '.png')
                and not p.parts[len(ROOT.parts)+1].startswith('W2-P2-1')):
            paths.append(p)
    paths += [p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.md', '.toml', '.txt')]
    paths = sorted(set(p for p in paths if p.is_file()))
    index = {p.relative_to(ROOT).as_posix(): vw.digest(p) for p in paths}
    save(MANIFEST, dict(created_utc=vw.now(), files=index))
    with tarfile.open(BUNDLE, 'w:gz', compresslevel=1) as tar:
        for p in paths:
            tar.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
        tar.add(MANIFEST, arcname='bundle_manifest.json')
    info = dict(bytes=BUNDLE.stat().st_size, sha256=vw.digest(BUNDLE), files=len(paths))
    save(WORK/'bundle_info.json', info)
    print(json.dumps(info))


def rent(offer_id=None):
    plan = approval()
    current = load(RENTAL)
    if current and current.get('status') in LIVE:
        raise RuntimeError('Уже есть живая аренда: '+str(current.get('instance_id')))
    history = load(WORK/'attempts.json', dict(attempts=[]))
    if len(history['attempts']) >= int(plan['max_rent_attempts']):
        raise RuntimeError('Исчерпан лимит попыток аренды из approval')
    offers = load(WORK/'offers.json', {}).get('offers', [])
    if offer_id is not None:
        offers = [o for o in offers if int(o['id']) == int(offer_id)]
    tried = {a['offer_id'] for a in history['attempts']}
    offers = [o for o in offers if o['id'] not in tried]
    if not offers:
        raise RuntimeError('Нет подходящего предложения в offers.json (выполните offers)')
    offer = offers[0]
    if float(offer['dph_total']) > float(plan['max_hourly_usd']):
        raise RuntimeError('Цена выше предела approval')
    result = vw.cli('create', 'instance', offer['id'], '--image', plan.get('image', 'ubuntu:22.04'),
                    '--disk', int(plan.get('disk_gb', 60)), '--ssh', '--direct', '--cancel-unavail',
                    '--label', 'submoon-w2-p2-1')
    iid = result.get('new_contract') if isinstance(result, dict) else None
    history['attempts'].append(dict(utc=vw.now(), offer_id=offer['id'], instance_id=iid))
    save(WORK/'attempts.json', history)
    if not isinstance(iid, int):
        raise RuntimeError('Не подтверждён create instance')
    record = dict(instance_id=iid, status='created', created_utc=vw.now(), created_epoch=time.time(),
                  deadline_epoch=time.time()+float(plan['max_hours'])*3600, offer=offer,
                  approval_ref=plan['user_message'], budget_usd=plan['budget_usd'])
    save(RENTAL, record)
    print(json.dumps(dict(instance_id=iid, offer_id=offer['id'], cpu=offer['cpu_name'],
                          effective=offer['cpu_cores_effective'], hourly=offer['dph_total']), ensure_ascii=False))


def wait(timeout_minutes=40):
    rental = load(RENTAL)
    started = time.time()
    while time.time()-started < timeout_minutes*60:
        info = vw.instance(rental['instance_id'])
        state = info.get('actual_status')
        print(vw.now(), 'actual_status =', state, flush=True)
        if state == 'running':
            rental['status'] = 'running'
            save(RENTAL, rental)
            return True
        time.sleep(30)
    print('Инстанс не перешёл в running за отведённое время — удаляю.')
    destroy()
    return False


def _workers(offer):
    """Верхняя граница процессов по предложению: доступные потоки и RAM (~450 МБ на процесс).
    На сервере дополнительно ограничивается числом физических ядер (SMT не используется)."""
    effective = int(offer.get('cpu_cores_effective') or 1)
    ram_mb = float(offer.get('cpu_ram') or 0)
    return max(1, min(effective, int(ram_mb/450))) if ram_mb else effective


def _start_script(rental):
    iid = int(rental['instance_id'])
    workers = _workers(rental['offer'])
    run_dir = f'runs/W2-P2-1-{iid}'
    deadline = rental['run_deadline_utc']
    return f'''#!/bin/bash
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
STATUS=/workspace/p2_status
fail() {{ echo "FAILED $1 $(date -u +%FT%TZ)" > $STATUS; exit 1; }}
echo "setup $(date -u +%FT%TZ)" > $STATUS
apt-get update -qq || fail apt-update
apt-get install -y -qq python3 python3-venv python3-dev build-essential git || fail apt-install
cd {REMOTE_ROOT}
python3 -m venv .venv || fail venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-w2.txt || fail pip
.venv/bin/python -m pip install --disable-pip-version-check -q --no-deps -e . || fail pip-project
.venv/bin/python -m pip freeze > /workspace/p2_pip_freeze.txt
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1
export W2_REMOTE_EXECUTION=1 W2_INSTANCE_ID={iid}
rm -f {STOP_FILE}
echo "gates $(date -u +%FT%TZ)" > $STATUS
.venv/bin/python -m pytest -q tests/unit/test_compiled_dop853.py tests/unit/test_admission_evaluators.py tests/unit/test_events_shared_samples.py tests/unit/test_native_ias15_guard.py || fail pytest
.venv/bin/python -m ruff check src scripts tests || fail ruff
# Диагностика W2-I015 (не гейт): реальный случай отказа B до 1.5 года новым стражем.
mkdir -p {run_dir}
.venv/bin/python -u scripts/diag_ias15_guard.py --output {run_dir}/diag_ias15_guard.json || echo "diag_ias15_guard: код $?"
# Короткий гейт: обязательный набор с B, расширение B и ступень вне обязательного набора.
.venv/bin/python -u scripts/admission_p2_horizons.py --limit 4 --years 0.01 0.03 0.1 --b-max-years 0.03 --core-years 0.01 0.03 --core-b-max 0.01 --concurrent --workers 8 --run-dir runs/W2-P2-1-quick-{iid} || fail quick
PHYS=$(lscpu -p=CORE,SOCKET | grep -v '^#' | sort -u | wc -l)
QUOTA=$(awk '$1!="max"{{printf "%d", $1/$2}}' /sys/fs/cgroup/cpu.max 2>/dev/null)
W={workers}
if [ "$PHYS" -gt 0 ] && [ "$PHYS" -lt "$W" ]; then W=$PHYS; fi
if [ -n "$QUOTA" ] && [ "$QUOTA" -gt 0 ] && [ "$QUOTA" -lt "$W" ]; then W=$QUOTA; fi
echo "running $(date -u +%FT%TZ) workers=$W physical=$PHYS quota=${{QUOTA:-none}} offer_limit={workers} deadline={deadline}" > $STATUS
.venv/bin/python -u scripts/admission_p2_horizons.py --years 1 10 100 1000 10000 --b-max-years 1000 --concurrent --workers $W --deadline-utc {deadline} --stop-file {STOP_FILE} --run-dir {run_dir}
echo "done exit=$? $(date -u +%FT%TZ)" > $STATUS
'''


def deploy():
    rental = load(RENTAL)
    if not rental or rental.get('status') not in ('running', 'created'):
        raise RuntimeError('Нет аренды в состоянии running')
    info = vw.instance(rental['instance_id'])
    if info.get('actual_status') != 'running':
        raise RuntimeError('Инстанс не running: '+str(info.get('actual_status')))
    plan = approval()
    hours = float(plan.get('run_deadline_hours', DEFAULT_RUN_DEADLINE_HOURS))
    if hours >= float(plan['max_hours']):
        raise ValueError('run_deadline_hours должен быть меньше max_hours')
    deadline = rental['created_epoch']+hours*3600
    if deadline-time.time() < 3*3600:
        raise RuntimeError('До срока стенда меньше 3 ч: развёртывание бессмысленно, сообщите пользователю')
    rental['run_deadline_utc'] = datetime.fromtimestamp(deadline, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    vw.WORK = WORK
    client = vw.connection(info)
    try:
        sftp = client.open_sftp()
        archive = '/workspace/p2_bundle_'+vw.digest(BUNDLE)[:16]+'.tar.gz'
        vw.remote_command(client, 'mkdir -p '+REMOTE_ROOT)
        vw.put_if_missing_or_identical(client, sftp, BUNDLE, archive)
        vw.remote_command(client, 'tar -xzf '+archive+' -C '+REMOTE_ROOT)
        remote_manifest = vw.remote_command(client, 'sha256sum '+REMOTE_ROOT+'/bundle_manifest.json').split()[0]
        if remote_manifest != vw.digest(MANIFEST):
            raise RuntimeError('SHA удалённого манифеста не совпал')
        log = f'/workspace/p2_{rental["instance_id"]}.log'
        if vw.remote_command(client, 'test ! -e '+log+' && echo absent').strip() != 'absent':
            raise RuntimeError('Удалённый лог уже существует: повторный deploy запрещён')
        script = WORK/f'start_{rental["instance_id"]}.sh'
        script.write_text(_start_script(rental), encoding='utf-8', newline='\n')
        vw.put_if_missing_or_identical(client, sftp, script, '/workspace/p2_start.sh')
        pid = vw.remote_command(client, f'nohup bash /workspace/p2_start.sh > {log} 2>&1 < /dev/null & echo $!')
        rental.update(status='deployed', deployed_utc=vw.now(), remote_pid=int(pid.strip()),
                      remote_log=log, workers=_workers(rental['offer']))
        save(RENTAL, rental)
        print(json.dumps(dict(instance_id=rental['instance_id'], remote_pid=rental['remote_pid'],
                              workers=rental['workers'], run_deadline_utc=rental['run_deadline_utc'])))
    finally:
        client.close()


def status():
    rental = load(RENTAL)
    if not rental:
        raise SystemExit('Нет записи аренды')
    vw.WORK = WORK
    out = dict(instance_id=rental['instance_id'], local_status=rental['status'],
               run_deadline_utc=rental.get('run_deadline_utc'))
    found = [x for x in vw.instances() if int(x.get('id', -1)) == int(rental['instance_id'])]
    if not found:
        out['provider'] = 'not_found'
        print(json.dumps(out, ensure_ascii=False))
        return out
    info = found[0]
    elapsed = (time.time()-rental['created_epoch'])/3600
    out.update(actual_status=info.get('actual_status'), elapsed_hours=round(elapsed, 2),
               hourly=rental['offer']['dph_total'], upper_cost_usd=round(elapsed*float(rental['offer']['dph_total']), 2),
               budget_usd=rental['budget_usd'])
    if info.get('actual_status') == 'running':
        try:
            with vw.connection(info) as client:
                run_dir = f'{REMOTE_ROOT}/runs/W2-P2-1-{rental["instance_id"]}'
                out['remote_stage'] = vw.remote_command(client, 'cat /workspace/p2_status 2>/dev/null || echo none', 20).strip()
                out['progress'] = vw.remote_command(client, f'cat {run_dir}/progress.json 2>/dev/null || echo none', 20).strip()[:1500]
                out['log_tail'] = vw.remote_command(client, f'tail -n 15 {rental.get("remote_log", "/dev/null")} 2>/dev/null', 20)[-2500:]
        except Exception as exc:
            out['ssh'] = type(exc).__name__
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def remote_stage(rental):
    info = vw.instance(int(rental['instance_id']))
    vw.WORK = WORK
    with vw.connection(info) as client:
        return vw.remote_command(client, 'cat /workspace/p2_status 2>/dev/null || echo none', 20).strip()


def wrapup():
    """Досрочный итог: стоп-файл; стенд останавливает задания (partial) и пишет summary.json."""
    rental = load(RENTAL)
    info = vw.instance(int(rental['instance_id']))
    vw.WORK = WORK
    with vw.connection(info) as client:
        stage = vw.remote_command(client, 'cat /workspace/p2_status 2>/dev/null || echo none', 20).strip()
        if not stage.startswith('running'):
            print(json.dumps(dict(stage=stage, action='стоп-файл не нужен'), ensure_ascii=False))
            return
        vw.remote_command(client, f'touch {STOP_FILE}', 20)
    rental['wrapup_utc'] = vw.now()
    save(RENTAL, rental)
    print(json.dumps(dict(stage=stage, action='стоп-файл создан; ждите этапа done (status), затем finish'),
                     ensure_ascii=False))


def download():
    rental = load(RENTAL)
    iid = int(rental['instance_id'])
    info = vw.instance(iid)
    vw.WORK = WORK
    pack = f'''import hashlib, json, pathlib, tarfile
root = pathlib.Path("{REMOTE_ROOT}")
files = [p for d in sorted((root/"runs").glob("W2-P2-1*{iid}")) for p in d.rglob("*") if p.is_file()]
extra = [pathlib.Path(x) for x in ("/workspace/p2_status", "{rental.get('remote_log', '')}", "/workspace/p2_pip_freeze.txt")]
with tarfile.open("/workspace/p2_results.tar.gz", "w:gz", compresslevel=1) as tar:
    for p in files:
        tar.add(p, arcname=p.relative_to(root).as_posix())
    for p in extra:
        if p.is_file():
            tar.add(p, arcname="remote_logs/"+p.name)
print(len(files))
'''
    with vw.connection(info) as client:
        sftp = client.open_sftp()
        with sftp.open('/workspace/p2_pack.py', 'w') as stream:
            stream.write(pack)
        count = vw.remote_command(client, 'python3 /workspace/p2_pack.py', timeout=900).strip()
        target = WORK/f'results_{iid}.tar.gz'
        sftp.get('/workspace/p2_results.tar.gz', str(target))
        remote_hash = vw.remote_command(client, 'sha256sum /workspace/p2_results.tar.gz').split()[0]
        sftp.close()
    if vw.digest(target) != remote_hash:
        raise RuntimeError('SHA выгруженного архива не совпал')
    extracted = 0
    with tarfile.open(target, 'r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            if name.startswith('remote_logs/'):
                destination = WORK/f'remote_logs_{iid}'/Path(name).name
            elif name.startswith('runs/W2-P2-1') and '..' not in Path(name).parts:
                destination = ROOT/name
            else:
                raise ValueError('Путь вне разрешённой области: '+name)
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(tar.extractfile(member).read())
            extracted += 1
    rental.update(downloaded_utc=vw.now(), result_archive_sha256=remote_hash, remote_files=int(count),
                  extracted_files=extracted, result_archive=str(target.relative_to(ROOT)))
    save(RENTAL, rental)
    print(json.dumps(dict(instance_id=iid, files=int(count), extracted=extracted, sha256=remote_hash)))


def destroy():
    rental = load(RENTAL)
    iid = int(rental['instance_id'])
    vw.cli('destroy', 'instance', iid)
    time.sleep(10)
    if any(int(x.get('id', -1)) == iid for x in vw.instances()):
        raise RuntimeError('Удаление инстанса не подтверждено')
    elapsed = (time.time()-rental['created_epoch'])/3600
    rental.update(status='destroyed_verified', destroyed_utc=vw.now(), elapsed_hours=round(elapsed, 3),
                  upper_cost_usd=round(elapsed*float(rental['offer']['dph_total']), 3))
    save(RENTAL, rental)
    print(json.dumps({k: rental[k] for k in ('instance_id', 'status', 'elapsed_hours', 'upper_cost_usd')}))


def finish(partial=False):
    stage = remote_stage(load(RENTAL))
    if not stage.startswith('done') and not partial:
        raise SystemExit(f'Удалённый этап: {stage!r}. Стенд не завершён: выполните wrapup и дождитесь done, '
                         'либо finish --partial (выгрузить имеющееся). Инстанс НЕ удалён.')
    try:
        download()
    except Exception as exc:
        rental = load(RENTAL)
        rental['download_error'] = type(exc).__name__+': '+str(exc)[:300]
        save(RENTAL, rental)
        raise SystemExit('Выгрузка не удалась; инстанс НЕ удалён. Повторите finish или по указанию — destroy.')
    destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('action', choices=('preflight', 'offers', 'prepare', 'rent', 'wait', 'deploy',
                                           'status', 'wrapup', 'download', 'finish', 'destroy'))
    parser.add_argument('--offer-id', type=int, default=None)
    parser.add_argument('--timeout-minutes', type=int, default=40)
    parser.add_argument('--partial', action='store_true', help='finish: выгрузить и удалить, даже если стенд не завершён')
    args = parser.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)
    {'preflight': preflight, 'offers': search_offers, 'prepare': prepare,
     'rent': lambda: rent(args.offer_id), 'wait': lambda: wait(args.timeout_minutes), 'deploy': deploy,
     'status': status, 'wrapup': wrapup, 'download': download,
     'finish': lambda: finish(args.partial), 'destroy': destroy}[args.action]()


if __name__ == '__main__':
    main()
