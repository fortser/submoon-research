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
  seed      — выборка готовых строк прежнего run для переиспользования (approval['seed'], W2-D021)
              -> scratch/p2_rental/seed_jobs.jsonl + seed_info.json (без затрат)
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
SEED = WORK/'seed_jobs.jsonl'
SEED_INFO = WORK/'seed_info.json'
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


def _api_key():
    """Ключ Vast.ai из стандартного каталога CLI; на диск проекта не копируется."""
    for path in (Path.home()/'.config/vastai/vast_api_key', Path.home()/'.vast_api_key'):
        if path.exists():
            text = path.read_text(encoding='utf-8').strip()
            return text.split('=', 1)[1].strip() if '=' in text else text
    return None


def _credit_usd():
    """Баланс через публичный REST (CLI 0.3.1 отдаёт 400 на `show user`)."""
    key = _api_key()
    if not key:
        return None
    request = urllib.request.Request('https://console.vast.ai/api/v0/users/current/',
        headers={'Authorization': 'Bearer '+key, 'Accept': 'application/json'})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=60) as response:
        data = json.loads(response.read().decode('utf-8', 'replace'))
    return data.get('credit')


def preflight():
    plan = approval()
    key = Path.home()/'.ssh/vast_agent'
    checks = dict(cli=bool(vw.CLI), ssh_key=key.exists(), approval=True)
    try:
        import paramiko  # noqa: F401
        checks['paramiko'] = True
    except ImportError:
        checks['paramiko'] = False
    try:
        credit = _credit_usd()
    except Exception as exc:  # noqa: BLE001
        checks['credit_error'] = type(exc).__name__
        credit = None
    checks['credit_usd'] = credit
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
    # Только подтверждённые входы load_cases из W2-конфигов (model, states, contrasts):
    # локальные бенчмарки runs/ и W0-сырьё (data/raw, кернелы) удалённому расчёту не нужны.
    import yaml  # noqa: E402
    for cfg_path in (ROOT/'configs/experiments').glob('W2_*.yaml'):
        try:
            cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(cfg, dict) and isinstance(cfg.get('inputs_sha256'), dict):
            for rel in cfg['inputs_sha256']:
                p = ROOT/rel
                if p.is_file():
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


# W2-D021: на гибридных Intel (P+E) длинные задания B идут в 2-2.5 раза медленнее на E-ядрах
# (P2-1 55200007: ~2700 против ~1100-1500 шагов/с). Режим pcores_only: по одному процессу на
# физическое P-ядро (/sys/devices/cpu_core/cpus), стенд под taskset; без P-ядер — FAILED pcores.
_PCORES_BLOCK = r"""PCPUS=$(python3 - <<'PY'
import os, re, subprocess
def parse(text):
    out = []
    for a, b in re.findall(r'(\d+)(?:-(\d+))?', text):
        out.extend(range(int(a), int(b or a)+1))
    return out
try:
    pset = set(parse(open('/sys/devices/cpu_core/cpus').read()))
except OSError:
    pset = set()
allowed = os.sched_getaffinity(0)
first = {}
for line in subprocess.check_output(['lscpu', '-p=CPU,CORE']).decode().splitlines():
    if line.startswith('#'):
        continue
    cpu, core = map(int, line.split(',')[:2])
    if cpu in pset and cpu in allowed and core not in first:
        first[core] = cpu
print(','.join(map(str, sorted(first.values()))))
PY
)
[ -n "$PCPUS" ] || fail pcores
NP=$(echo "$PCPUS" | tr ',' '\n' | wc -l)
W=$NP
""" + 'PIN="taskset -c $PCPUS"'


def _select_seed(lines, variants, years):
    """Строки прежнего jobs.jsonl для переиспользования (W2-D021): только заданные варианты и
    ступени, только завершённые, с отпечатком, без дубликатов. Стенд сам примет лишь строки,
    чей job_fingerprint совпадёт с текущим кодом и окружением; остальные пересчитает."""
    variants, years = set(variants), {float(y) for y in years}
    selected, keys = [], set()
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get('variant') not in variants or float(row.get('years')) not in years:
            continue
        key = (row.get('orbit_id'), float(row['years']), row['variant'])
        if row.get('run_status') != 'completed' or not row.get('job_fingerprint'):
            raise ValueError('Строка для seed не завершена или без отпечатка: '+str(key))
        if key in keys:
            raise ValueError('Дубликат строки для seed: '+str(key))
        keys.add(key)
        selected.append(line.strip())
    return selected


def make_seed():
    plan = approval()
    spec = plan.get('seed')
    if not spec:
        raise ValueError('В approval нет раздела seed')
    source = ROOT/spec['source_jobs']
    digest = vw.digest(source)
    if spec.get('source_sha256') and digest != spec['source_sha256']:
        raise ValueError('SHA источника seed не совпал с approval')
    lines = _select_seed(source.read_text(encoding='utf-8').splitlines(), spec['variants'], spec['years'])
    if len(lines) != int(spec['expected_rows']):
        raise ValueError(f'Строк для seed {len(lines)}, ожидалось {spec["expected_rows"]}')
    SEED.write_text('\n'.join(lines)+'\n', encoding='utf-8', newline='\n')
    info = dict(utc=vw.now(), decision=spec.get('decision', 'W2-D021'), source_jobs=spec['source_jobs'],
                source_sha256=digest, variants=list(spec['variants']), years=list(spec['years']),
                rows=len(lines), seed_sha256=vw.digest(SEED))
    save(SEED_INFO, info)
    print(json.dumps(info, ensure_ascii=False))
    return info


def _start_script(rental):
    iid = int(rental['instance_id'])
    workers = _workers(rental['offer'])
    run_dir = f'runs/W2-P2-1-{iid}'
    deadline = rental['run_deadline_utc']
    pcores_block = _PCORES_BLOCK if rental.get('pcores_only') else '# pcores_only выключен: все физические ядра'
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
.venv/bin/python -m pytest -q tests/unit/test_compiled_dop853.py tests/unit/test_admission_evaluators.py tests/unit/test_events_shared_samples.py tests/unit/test_native_ias15_guard.py tests/unit/test_high_e_rule.py tests/unit/test_horizon_independence.py || fail pytest
.venv/bin/python -m ruff check src scripts tests || fail ruff
# Диагностика W2-I015 (не гейт): реальный случай отказа B до 1.5 года новым стражем.
mkdir -p {run_dir}
.venv/bin/python -u scripts/diag_ias15_guard.py --output {run_dir}/diag_ias15_guard.json || echo "diag_ias15_guard: код $?"
# Диагностика W2-I017 (не гейт): страж на dt_last_done при t0 до 10000 лет.
.venv/bin/python -u scripts/diag_ias15_guard_dtdone.py --output {run_dir}/diag_ias15_guard_dtdone.json || echo "diag_ias15_guard_dtdone: код $?"
# Короткий гейт: обязательный набор с B, расширение B и ступень вне обязательного набора.
.venv/bin/python -u scripts/admission_p2_horizons.py --limit 4 --years 0.01 0.03 0.1 --b-max-years 0.03 --core-years 0.01 0.03 --core-b-max 0.01 --concurrent --workers 8 --run-dir runs/W2-P2-1-quick-{iid} || fail quick
PHYS=$(lscpu -p=CORE,SOCKET | grep -v '^#' | sort -u | wc -l)
QUOTA=$(awk '$1!="max"{{printf "%d", $1/$2}}' /sys/fs/cgroup/cpu.max 2>/dev/null)
W={workers}
if [ "$PHYS" -gt 0 ] && [ "$PHYS" -lt "$W" ]; then W=$PHYS; fi
if [ -n "$QUOTA" ] && [ "$QUOTA" -gt 0 ] && [ "$QUOTA" -lt "$W" ]; then W=$QUOTA; fi
PIN=""
{pcores_block}
SEED=$(test -f {run_dir}/jobs.jsonl && wc -l < {run_dir}/jobs.jsonl || echo 0)
echo "running $(date -u +%FT%TZ) workers=$W physical=$PHYS quota=${{QUOTA:-none}} offer_limit={workers} pin=${{PCPUS:-none}} seed_rows=$SEED deadline={deadline}" > $STATUS
$PIN .venv/bin/python -u scripts/admission_p2_horizons.py --years 1 10 100 1000 10000 --b-max-years 1000 --concurrent --workers $W --deadline-utc {deadline} --stop-file {STOP_FILE} --run-dir {run_dir}
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
        if plan.get('seed'):
            info = load(SEED_INFO)
            if not info or not SEED.exists() or vw.digest(SEED) != info.get('seed_sha256'):
                raise RuntimeError('Нет согласованного seed: сначала действие seed')
            remote_dir = f'{REMOTE_ROOT}/runs/W2-P2-1-{rental["instance_id"]}'
            remote_jobs = remote_dir+'/jobs.jsonl'
            vw.remote_command(client, 'mkdir -p '+remote_dir)
            if vw.remote_command(client, 'test ! -e '+remote_jobs+' && echo absent').strip() != 'absent':
                raise RuntimeError('Удалённый jobs.jsonl уже существует: seed не накладывается поверх')
            sftp.put(str(SEED), remote_jobs)
            if vw.remote_command(client, 'sha256sum '+remote_jobs).split()[0] != info['seed_sha256']:
                raise RuntimeError('SHA seed на сервере не совпал')
            rental['seed'] = info
        rental['pcores_only'] = bool(plan.get('pcores_only'))
        script = WORK/f'start_{rental["instance_id"]}.sh'
        script.write_text(_start_script(rental), encoding='utf-8', newline='\n')
        vw.put_if_missing_or_identical(client, sftp, script, '/workspace/p2_start.sh')
        pid = vw.remote_command(client, f'nohup bash /workspace/p2_start.sh > {log} 2>&1 < /dev/null & echo $!')
        rental.update(status='deployed', deployed_utc=vw.now(), remote_pid=int(pid.strip()),
                      remote_log=log, workers_limit=_workers(rental['offer']), workers=None)
        save(RENTAL, rental)
        print(json.dumps(dict(instance_id=rental['instance_id'], remote_pid=rental['remote_pid'],
                              workers_limit=rental['workers_limit'], pcores_only=rental['pcores_only'],
                              run_deadline_utc=rental['run_deadline_utc'])))
    finally:
        client.close()


def _parse_running_stage(text):
    """Фактические параметры запуска из строки этапа «running … workers=N … pin=… seed_rows=N».
    В rental.json прежде попадал только предел по предложению (32 при фактических 8, W2-C034)."""
    if not text.startswith('running '):
        return None
    fields = dict(m.groups() for m in re.finditer(r'(\w+)=(\S+)', text))
    actual = {}
    if 'workers' in fields:
        actual['workers'] = int(fields['workers'])
    pin = re.search(r'pin=(?:taskset -c )?(\S+)', text)  # прежний формат: pin=taskset -c 0,2,…
    if pin:
        actual['pin'] = None if pin.group(1) == 'none' else pin.group(1)
    if 'seed_rows' in fields:
        actual['seed_rows'] = int(fields['seed_rows'])
    return actual


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
                actual = _parse_running_stage(out['remote_stage'])
                if actual and any(rental.get(k) != v for k, v in actual.items()):
                    rental.update(actual)  # фактические workers/pin/seed со строки этапа
                    save(RENTAL, rental)
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
    stats = _extract_results(target, iid)
    rental.update(downloaded_utc=vw.now(), result_archive_sha256=remote_hash, remote_files=int(count),
                  extracted_files=stats['written'], extract_stats=stats,
                  result_archive=str(target.relative_to(ROOT)))
    save(RENTAL, rental)
    print(json.dumps(dict(instance_id=iid, files=int(count), sha256=remote_hash, **stats)))


def _extract_results(archive, iid, *, root=None, work=None, stamp=None):
    """Распаковка архива результатов со сверкой SHA-256 каждого файла.

    Прежде существующий файл молча пропускался: устаревший jobs.jsonl (17:41, 9.1 МБ против
    11.4 МБ на сервере) и пустой jobs.jsonl после оборванной распаковки не обновлялись.
    Теперь совпадающий по SHA файл не трогается, отличающийся переносится в
    <work>/superseded_<iid>_<stamp>/ с сохранением пути и заменяется; запись атомарная.
    """
    import hashlib
    import shutil

    root = Path(root or ROOT)
    work = Path(work or WORK)
    stamp = stamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    stats = dict(written=0, new=0, unchanged=0, replaced=0, superseded_dir=None)
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            parts = Path(name).parts
            if '..' in parts or Path(name).is_absolute():
                raise ValueError('Путь вне разрешённой области: '+name)
            if name.startswith('remote_logs/'):
                destination = work/f'remote_logs_{iid}'/Path(name).name
            elif name.startswith('runs/W2-P2-1'):
                destination = root/name
            else:
                raise ValueError('Путь вне разрешённой области: '+name)
            data = tar.extractfile(member).read()
            if destination.exists():
                if hashlib.sha256(destination.read_bytes()).hexdigest() == hashlib.sha256(data).hexdigest():
                    stats['unchanged'] += 1
                    continue
                keep = work/f'superseded_{iid}_{stamp}'/destination.relative_to(
                    root if destination.is_relative_to(root) else work)
                keep.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(keep))
                stats['replaced'] += 1
                stats['superseded_dir'] = str(work/f'superseded_{iid}_{stamp}')
            else:
                stats['new'] += 1
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name+'.partial')
            temporary.write_bytes(data)
            temporary.replace(destination)
            stats['written'] += 1
    return stats


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
    parser.add_argument('action', choices=('preflight', 'offers', 'prepare', 'seed', 'rent', 'wait', 'deploy',
                                           'status', 'wrapup', 'download', 'finish', 'destroy'))
    parser.add_argument('--offer-id', type=int, default=None)
    parser.add_argument('--timeout-minutes', type=int, default=40)
    parser.add_argument('--partial', action='store_true', help='finish: выгрузить и удалить, даже если стенд не завершён')
    args = parser.parse_args(argv)
    WORK.mkdir(parents=True, exist_ok=True)
    {'preflight': preflight, 'offers': search_offers, 'prepare': prepare, 'seed': make_seed,
     'rent': lambda: rent(args.offer_id), 'wait': lambda: wait(args.timeout_minutes), 'deploy': deploy,
     'status': status, 'wrapup': wrapup, 'download': download,
     'finish': lambda: finish(args.partial), 'destroy': destroy}[args.action]()


if __name__ == '__main__':
    main()
