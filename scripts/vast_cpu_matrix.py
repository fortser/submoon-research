"""Транспорт CPU-матрицы W2-T006: по одной аренде на CPU-класс.

Переиспользует низкоуровневые помощники `vast_w2` (SSH, SHA, очищенные ответы),
но ведёт отдельный реестр аренд на каждую машину и запускает машинный бенчмарк
`bench_cpu_matrix.py`. Платные действия выполняются только при наличии
`scratch/cpu_matrix/approval.json` с явным согласием пользователя.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vast_w2 as vw  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT/'scratch/cpu_matrix'
SHARED = MATRIX/'shared'
REGISTRY = MATRIX/'machines.json'
APPROVAL = MATRIX/'approval.json'
BUNDLES = 'https://console.vast.ai/api/v0/bundles/'


def load(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def work(key):
    folder = MATRIX/key
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def use(key):
    vw.WORK = work(key)
    return vw.WORK


def registry():
    return load(REGISTRY, {'machines': {}})


def approval():
    value = load(APPROVAL)
    if not value or value.get('approved') is not True or not value.get('user_message'):
        raise ValueError('Нет явного пользовательского разрешения в '+str(APPROVAL))
    return value


def list_instances():
    value = vw.cli('show', 'instances')
    return value if isinstance(value, list) else value.get('instances', [])


def instance_state(iid):
    for item in list_instances():
        if int(item.get('id', -1)) == int(iid):
            return item
    return None


def destroy_if_present(iid):
    if instance_state(iid) is not None:
        vw.cli('destroy', 'instance', iid)
    return instance_state(iid) is None


def candidates(machine):
    if machine.get('candidates'):
        return [dict(c) for c in machine['candidates']]
    return [dict(offer_id=machine['offer_id'], machine_id=machine['machine_id'])]


def fetch_offers(machine):
    body = {'rentable': {'eq': True}, 'rented': {'eq': False}, 'type': 'on-demand',
            'allocated_storage': 40, 'limit': 10, 'cpu_arch': {'eq': 'amd64'},
            'disk_space': {'gte': 20}, 'machine_id': {'eq': int(machine['machine_id'])}}
    request = urllib.request.Request(BUNDLES, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    opened = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=90)
    with opened as response:
        data = json.loads(response.read().decode('utf-8', 'replace'))
    offers = data.get('offers', []) if isinstance(data, dict) else []
    return [o for o in offers if int(o.get('id', -1)) == int(machine['offer_id'])]


def package_min():
    """Собирает облегчённый bundle: без data/interim и крупных файлов runs.

    Пакет должен быть достаточным для pytest/validate/smoke и load_cases,
    но не тащить сотни МБ прежних траекторий.
    """
    roots = ('src', 'scripts', 'tests', 'configs', 'docs', 'tracking', 'references',
             'reports', 'results', 'data/raw', 'data/processed', 'data/kernels',
             'data/manifests', 'data/acquisition', 'data/interim', 'arXiv-2609.03564v1')
    paths = [p for name in roots for p in (ROOT/name).rglob('*') if p.is_file()
             and '__pycache__' not in p.parts and 'vast_market' not in p.parts
             and p.suffix not in ('.pyc', '.pem', '.key') and p.name != '.write.lock']
    for p in (ROOT/'runs').rglob('*'):
        if (p.is_file() and p.stat().st_size <= 2*2**20
                and p.suffix not in ('.gz', '.zip', '.npz', '.parquet', '.png')
                and '__pycache__' not in p.parts):
            paths.append(p)
    paths += [p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.md', '.toml', '.bat')]
    paths += [p for p in (ROOT/'VastAI_logs').rglob('*') if p.is_file()]
    paths += [ROOT/'requirements-w2.txt', ROOT/'math.txt', ROOT/'good_cpu.txt']
    # Все существующие refs трекинга (мелкие), иначе удалённый project validate падает.
    import glob as _glob
    for journal in _glob.glob(str(ROOT/'tracking/stages/*/journal.jsonl')):
        for line in Path(journal).read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            record = json.loads(line)['record']
            for ref in list(record.get('refs', [])) + ([record['detail_path']]
                                                       if record.get('detail_path') else []):
                candidate = ROOT/ref
                if (candidate.is_file() and candidate.stat().st_size <= 2*2**20
                        and 'scratch/cpu_matrix/shared' not in candidate.parts):
                    paths.append(candidate)
    paths = sorted(set(p for p in paths if p.is_file()))
    index = {p.relative_to(ROOT).as_posix(): vw.digest(p) for p in paths}
    vw.save(SHARED/'bundle_manifest.json', dict(created_utc=vw.now(), files=index))
    with tarfile.open(SHARED/'bundle.tar.gz', 'w:gz', compresslevel=1) as tar:
        for p in paths:
            tar.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
        tar.add(SHARED/'bundle_manifest.json', arcname='bundle_manifest.json')
    out = dict(bytes=(SHARED/'bundle.tar.gz').stat().st_size,
               sha256=vw.digest(SHARED/'bundle.tar.gz'), files=len(paths))
    vw.save(SHARED/'bundle_info.json', out)
    print(json.dumps(out))


def prepare():
    MATRIX.mkdir(parents=True, exist_ok=True)
    SHARED.mkdir(parents=True, exist_ok=True)
    package_min()


def verify(key):
    import platform as _platform
    rental = load(work(key)/'rental.json')
    iid = int(rental['instance_id'])
    expected = vw.digest(SHARED/'bundle_manifest.json')
    info = vw.instance(iid)
    with vw.connection(info) as client:
        remote = vw.remote_command(client,
            'echo HOST=$(hostname); echo OS=$(uname -s); echo ARCH=$(uname -m); '
            'echo ID=$(cat /workspace/.w2_instance_id 2>/dev/null); '
            'echo MANIFEST=$(sha256sum /workspace/submoon-research/bundle_manifest.json 2>/dev/null | cut -d" " -f1)',
            timeout=30)
    values = dict(line.split('=', 1) for line in remote.strip().splitlines() if '=' in line)
    problems = []
    if values.get('OS') != 'Linux':
        problems.append('remote OS is not Linux')
    if values.get('ID') != str(iid):
        problems.append('instance sentinel mismatch')
    if values.get('MANIFEST') != expected:
        problems.append('remote bundle manifest SHA mismatch')
    if values.get('HOST', '').lower() == _platform.node().lower():
        problems.append('remote host equals local host')
    print(json.dumps(dict(host=values.get('HOST'), os=values.get('OS'), arch=values.get('ARCH'),
                          sentinel=values.get('ID'), manifest=values.get('MANIFEST'),
                          expected=expected, problems=problems)))
    if problems:
        raise RuntimeError('Remote verification failed: '+'; '.join(problems))


def rent(key):
    plan = approval()
    machine = plan['machines'][key]
    machine.setdefault('key', key)
    machine['key'] = key
    current = registry()
    live = [k for k, v in current['machines'].items() if v.get('status') not in
            ('destroyed_verified', 'failed', 'gone', 'error', 'timeout')]
    if len(live) >= int(plan['max_instances']):
        raise RuntimeError('Достигнут предел одновременных инстансов: '+str(live))
    entry = current['machines'].setdefault(key, {})
    attempted = set(entry.get('attempted', []))
    chosen = None
    for candidate in candidates(machine):
        if candidate['offer_id'] in attempted:
            continue
        for offer in fetch_offers(candidate):
            if not offer.get('rentable') or offer.get('rented'):
                continue
            price = float(offer.get('dph_total') or 9e9)
            if price > float(machine['max_hourly_usd']):
                continue
            if int(offer.get('cpu_cores_effective') or 0) < int(machine.get('min_effective', 1)):
                continue
            chosen = offer
            break
        if chosen:
            break
    if not chosen:
        raise RuntimeError('Нет ни одного доступного кандидата для '+key)
    offer = chosen
    price = float(offer.get('dph_total') or 9e9)
    use(key)
    result = vw.cli('create', 'instance', offer['id'], '--image', 'ubuntu:22.04',
                    '--disk', 40, '--ssh', '--direct', '--cancel-unavail',
                    '--label', 'submoon-cpumatrix-'+key)
    iid = result.get('new_contract')
    if not result.get('success') or not isinstance(iid, int):
        raise RuntimeError('Не подтверждён create instance')
    record = dict(instance_id=iid, key=key, created_utc=vw.now(), created_epoch=time.time(),
                  deadline_epoch=time.time()+float(plan['max_hours_per_instance'])*3600,
                  status='created', approval_ref=plan.get('user_message'), offer=offer,
                  max_hourly_usd=machine['max_hourly_usd'], ceiling_usd=machine['ceiling_usd'])
    save(work(key)/'rental.json', record)
    entry.update(instance_id=iid, status='created', machine_id=offer['machine_id'],
                 offer_id=offer['id'], cpu_name=offer.get('cpu_name'), hourly=price,
                 effective_cpus=offer.get('cpu_cores_effective'),
                 created_utc=record['created_utc'], deadline_epoch=record['deadline_epoch'],
                 attempted=sorted(attempted | {offer['id']}))
    save(REGISTRY, current)
    log = work(key)/'watchdog.log'
    watch = subprocess.Popen([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()),
                              'watchdog', '--key', key], cwd=ROOT,
                             stdout=log.open('a', encoding='utf-8'), stderr=subprocess.STDOUT,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    record['watchdog_pid'] = watch.pid
    save(work(key)/'rental.json', record)
    print(json.dumps(dict(key=key, instance_id=iid, hourly=price,
                          effective_cpus=offer.get('cpu_cores_effective'))))


def _remote_start_script(rental, effective, seconds):
    iid = int(rental['instance_id'])
    return ('#!/bin/bash\nset -euo pipefail\nexport DEBIAN_FRONTEND=noninteractive\n'
            'apt-get update -qq\n'
            'apt-get install -y -qq python3 python3-venv python3-dev build-essential git\n'
            'cd /workspace/submoon-research\npython3 -m venv .venv\n'
            '.venv/bin/python -m pip install --disable-pip-version-check -r requirements-w2.txt\n'
            '.venv/bin/python -m pip install --no-deps -e .\n'
            'export W2_REMOTE_EXECUTION=1\n'
            f'export W2_INSTANCE_ID={iid}\n'
            f'export W2_EFFECTIVE_CPUS={int(effective)}\n'
            'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1\n'
            'echo "=== GATE warmup ==="\n'
            '.venv/bin/python -c "import sys; sys.path.insert(0, \'src\'); '
            'from submoon_research.workflows.w2_comparison import warmup; print(\'WARMUP\', warmup())"\n'
            'echo "=== GATE pytest (без tests/physics: известный Numba-краш на этих хостах) ==="\n'
            '.venv/bin/python -m pytest tests/contracts tests/integration tests/unit tests/regression -q\n'
            'echo "=== GATE ruff ==="\n.venv/bin/python -m ruff check src scripts tests\n'
            'echo "=== GATE validate ==="\n.venv/bin/python scripts/project.py validate\n'
            'echo "=== GATE smoke ==="\n.venv/bin/python scripts/project.py smoke\n'
            'echo "=== BENCH campaign ==="\n'
            f'.venv/bin/python -u scripts/bench_cpu_matrix.py campaign --workers 512 --seconds {int(seconds)}\n')


def mark_failed(key, reason):
    current = registry()
    entry = current['machines'].setdefault(key, {})
    entry['status'] = reason
    save(REGISTRY, current)
    rental = load(work(key)/'rental.json')
    if rental:
        rental['status'] = reason
        rental['failed_utc'] = vw.now()
        save(work(key)/'rental.json', rental)


def wait_ready(key, timeout_minutes=40, poll_seconds=60):
    rental = load(work(key)/'rental.json')
    iid = rental['instance_id']
    deadline = time.time()+timeout_minutes*60
    last = None
    while time.time() < deadline:
        state = instance_state(iid)
        if state is None:
            print(vw.now(), key, iid, 'gone (провайдер отозвал инстанс)', flush=True)
            return 'gone'
        status = state.get('actual_status')
        message = state.get('status_msg') or state.get('cur_state')
        if (status, message) != last:
            print(vw.now(), key, iid, status, message, flush=True)
            last = (status, message)
        if status == 'running':
            return 'running'
        if status in ('error', 'exited', 'offline'):
            return 'error'
        time.sleep(poll_seconds)
    return 'timeout'


def watch(key, timeout_minutes=40, max_attempts=3):
    for attempt in range(1, max_attempts+1):
        rent(key)
        rental = load(work(key)/'rental.json')
        iid = rental['instance_id']
        result = wait_ready(key, timeout_minutes)
        if result == 'running':
            print(json.dumps(dict(key=key, instance_id=iid, status='running', attempt=attempt)))
            return 0
        print(vw.now(), key, 'attempt', attempt, 'failed:', result, flush=True)
        destroy_if_present(iid)
        mark_failed(key, result)
    raise RuntimeError('Ни один кандидат для '+key+' не поднялся за '+str(max_attempts)+' попыток')


def _remote_run_script(rental, effective, seconds, checks=True):
    iid = int(rental['instance_id'])
    lines = ['#!/bin/bash', 'set -euo pipefail', 'cd /workspace/submoon-research',
             'export W2_REMOTE_EXECUTION=1', f'export W2_INSTANCE_ID={iid}',
             f'export W2_EFFECTIVE_CPUS={int(effective)}',
             'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1']
    if checks:
        lines += ['echo "=== GATE warmup ==="',
                  '.venv/bin/python -c "import sys; sys.path.insert(0, \'src\'); '
                  'from submoon_research.workflows.w2_comparison import warmup; print(\'WARMUP\', warmup())"',
                  'echo "=== GATE pytest (без tests/physics) ==="',
                  '.venv/bin/python -m pytest tests/contracts tests/integration tests/unit tests/regression -q',
                  'echo "=== GATE ruff ==="',
                  '.venv/bin/python -m ruff check src scripts tests',
                  'echo "=== GATE validate ==="',
                  '.venv/bin/python scripts/project.py validate',
                  'echo "=== GATE smoke ==="', '.venv/bin/python scripts/project.py smoke']
    lines += ['echo "=== BENCH campaign ==="',
              f'.venv/bin/python -u scripts/bench_cpu_matrix.py campaign --workers 512 --seconds {int(seconds)}']
    return '\n'.join(lines)+'\n'


def update(key, seconds=14400, checks=True):
    """Перезаливает обновлённый bundle поверх проекта и перезапускает прогон."""
    rental_path = work(key)/'rental.json'
    rental = load(rental_path)
    if not rental or rental.get('status') not in ('deployed', 'created'):
        raise RuntimeError('Нет активной аренды для обновления: '+key)
    vw.WORK = work(key)
    info = vw.instance(rental['instance_id'])
    if info.get('actual_status') != 'running':
        print(json.dumps(dict(status=info.get('actual_status'))))
        return
    effective = int(rental['offer'].get('cpu_cores_effective') or 1)
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    client = vw.connection(info)
    try:
        # Останавливаем предыдущую кампанию, чтобы не запускать две одновременно.
        vw.remote_command(client, "pkill -f '[b]ench_cpu_matrix.py' || true", timeout=20)
        sftp = client.open_sftp()
        project_root = '/workspace/submoon-research'
        archive = '/workspace/bench_update_'+stamp+'_'+vw.digest(SHARED/'bundle.tar.gz')[:12]+'.tar.gz'
        vw.put_if_missing_or_identical(client, sftp, SHARED/'bundle.tar.gz', archive)
        vw.remote_command(client, 'tar -xzf '+archive+' -C '+project_root, timeout=300)
        expected = vw.digest(SHARED/'bundle_manifest.json')
        actual = vw.remote_command(client, 'sha256sum '+project_root+'/bundle_manifest.json').split()[0]
        if actual != expected:
            raise RuntimeError('SHA удалённого manifest после обновления не совпал')
        script_file = work(key)/('update_'+stamp+'.sh')
        script_file.write_text(_remote_run_script(rental, effective, seconds, checks),
                               encoding='utf-8', newline='\n')
        remote_script = '/workspace/update_'+stamp+'.sh'
        remote_log = '/workspace/bench_'+str(rental['instance_id'])+'_'+stamp+'.log'
        vw.put_if_missing_or_identical(client, sftp, script_file, remote_script)
        pid = vw.remote_command(client, 'nohup bash '+remote_script+' > '+remote_log+' 2>&1 < /dev/null & echo $!')
        rental.update(status='deployed', remote_pid=int(pid.strip()), remote_log_path=remote_log,
                      last_update_utc=vw.now())
        save(rental_path, rental)
        print(json.dumps(dict(key=key, instance_id=rental['instance_id'],
                              remote_pid=rental['remote_pid'], log=remote_log)))
    finally:
        client.close()


def deploy(key, seconds=14400):
    rental_path = work(key)/'rental.json'
    rental = load(rental_path)
    if not rental:
        raise ValueError('Нет записи аренды для '+key)
    if rental.get('status') not in ('created', 'deployed'):
        raise RuntimeError('Состояние аренды не допускает deploy: '+str(rental.get('status')))
    vw.WORK = work(key)
    info = vw.instance(rental['instance_id'])
    if info.get('actual_status') != 'running':
        print(json.dumps(dict(status=info.get('actual_status'))))
        return
    effective = int(rental['offer'].get('cpu_cores_effective') or 1)
    client = vw.connection(info)
    try:
        sftp = client.open_sftp()
        project_root = '/workspace/submoon-research'
        try:
            sftp.stat(project_root)
            exists = True
        except OSError:
            exists = False
        if not exists:
            archive = '/workspace/w2_bundle_'+vw.digest(SHARED/'bundle.tar.gz')+'.tar.gz'
            vw.remote_command(client, 'mkdir -p /workspace')
            vw.put_if_missing_or_identical(client, sftp, SHARED/'bundle.tar.gz', archive)
            vw.remote_command(client, 'mkdir -p '+project_root)
            vw.remote_command(client, 'tar -xzf '+archive+' -C '+project_root)
        expected = vw.digest(SHARED/'bundle_manifest.json')
        actual = vw.remote_command(client, 'sha256sum '+project_root+'/bundle_manifest.json').split()[0]
        if actual != expected:
            raise RuntimeError('SHA удалённого bundle_manifest не совпал')
        vw.remote_command(client, 'echo %d > /workspace/.w2_instance_id && hostname > /workspace/.w2_hostname'
                          % int(rental['instance_id']))
        start = MATRIX/key/('start_'+str(rental['instance_id'])+'.sh')
        start.write_text(_remote_start_script(rental, effective, seconds), encoding='utf-8',
                         newline='\n')
        remote_start = '/workspace/start_'+str(rental['instance_id'])+'.sh'
        remote_log = '/workspace/bench_'+str(rental['instance_id'])+'.log'
        if vw.remote_command(client, 'test ! -e '+remote_log+' && echo absent').strip() != 'absent':
            raise RuntimeError('Удалённый лог уже существует; перезапись запрещена')
        vw.put_if_missing_or_identical(client, sftp, start, remote_start)
        pid = vw.remote_command(client, 'nohup bash '+remote_start+' > '+remote_log+' 2>&1 < /dev/null & echo $!')
        rental.update(status='deployed', remote_pid=int(pid.strip()), deployed_utc=vw.now(),
                      remote_log_path=remote_log)
        save(rental_path, rental)
        reg = registry()
        reg['machines'][key].update(status='deployed', remote_pid=int(pid.strip()))
        save(REGISTRY, reg)
        print(json.dumps(dict(key=key, instance_id=rental['instance_id'], remote_pid=rental['remote_pid'])))
    finally:
        client.close()


def status(key):
    rental = load(work(key)/'rental.json')
    vw.WORK = work(key)
    info = vw.instance(rental['instance_id'])
    clean = {k: info.get(k) for k in ('id', 'actual_status', 'cpu_cores_effective', 'cpu_ram',
                                      'dph_total', 'disk_space')}
    clean['key'] = key
    clean['elapsed_hours'] = (time.time()-rental['created_epoch'])/3600
    clean['upper_usd'] = clean['elapsed_hours']*float(rental['offer']['dph_total'])
    if info.get('actual_status') == 'running':
        try:
            with vw.connection(info) as client:
                clean['log_tail'] = vw.remote_command(
                    client, 'tail -n 12 '+rental.get('remote_log_path', ''), timeout=20)
        except Exception as exc:
            clean['ssh_status'] = type(exc).__name__
    print(json.dumps(clean, ensure_ascii=False))


def download(key, client, rental):
    log_path = rental.get('remote_log_path', '')
    script = '''import pathlib, tarfile, hashlib, json
root=pathlib.Path('/workspace/submoon-research')
folders=[p for p in (root/'runs').iterdir() if p.name.startswith('W2-longbench') and p.stat().st_mtime >= START]
files=[p for d in folders for p in d.rglob('*') if p.is_file()]
hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
with tarfile.open('/workspace/bench_results.tar.gz','w:gz',compresslevel=1) as tar:
 for p in files: tar.add(p,arcname=p.relative_to(root).as_posix())
 log=pathlib.Path('__LOG__')
 if log.exists(): tar.add(str(log),arcname='bench_execution.log')
print(len(files))
'''.replace('START', str(rental['created_epoch']-5)).replace('__LOG__', log_path)
    sftp = client.open_sftp()
    with sftp.open('/workspace/bench_pack_results.py', 'w') as stream:
        stream.write(script)
    sftp.close()
    vw.remote_command(client, 'python3 /workspace/bench_pack_results.py', timeout=120)
    sftp = client.open_sftp()
    target = work(key)/'results.tar.gz'
    sftp.get('/workspace/bench_results.tar.gz', str(target))
    sftp.close()
    remote_hash = vw.remote_command(client, 'sha256sum /workspace/bench_results.tar.gz').split()[0]
    if vw.digest(target) != remote_hash:
        raise RuntimeError('SHA выгруженного архива не совпал')
    extracted = 0
    with tarfile.open(target, 'r:gz') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name
            data = tar.extractfile(member).read()
            if name == 'bench_execution.log':
                (work(key)/'execution.log').write_bytes(data)
                continue
            destination = (ROOT/name).resolve()
            if not destination.is_relative_to((ROOT/'runs').resolve()) or not name.startswith('runs/W2-longbench'):
                raise ValueError('Путь результата вне разрешённой области: '+name)
            if destination.exists():
                # Файл уже есть локально (в т.ч. из bundle) — не перезаписываем.
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            extracted += 1
    rental.update(downloaded_utc=vw.now(), result_archive_sha256=remote_hash, files=extracted)
    save(work(key)/'rental.json', rental)
    return extracted


def finish(key, deadline=False):
    rental = load(work(key)/'rental.json')
    vw.WORK = work(key)
    iid = rental['instance_id']
    found = [x for x in vw.instances() if x['id'] == iid]
    if found:
        try:
            with vw.connection(found[0]) as client:
                if deadline:
                    vw.remote_command(client, "pkill -f '[b]ench_cpu_matrix.py' || true", timeout=15)
                download(key, client, rental)
        except Exception as exc:
            rental['download_error'] = type(exc).__name__+': '+str(exc)
            save(work(key)/'rental.json', rental)
    else:
        rental['download_error'] = 'instance_not_found'
    vw.cli('destroy', 'instance', iid)
    if any(x['id'] == iid for x in vw.instances()):
        raise RuntimeError('Удаление инстанса не подтверждено')
    rental.update(status='destroyed_verified', destroyed_utc=vw.now(),
                  elapsed_hours=(time.time()-rental['created_epoch'])/3600)
    rental['compute_storage_upper_usd'] = rental['elapsed_hours']*float(rental['offer']['dph_total'])
    save(work(key)/'rental.json', rental)
    reg = registry()
    reg['machines'][key].update(status='destroyed_verified',
                                elapsed_hours=rental['elapsed_hours'],
                                upper_usd=rental['compute_storage_upper_usd'])
    save(REGISTRY, reg)
    print(json.dumps({k: rental[k] for k in ('key', 'instance_id', 'status', 'elapsed_hours',
                                             'compute_storage_upper_usd')}))


def watchdog(key):
    while True:
        rental = load(work(key)/'rental.json')
        if not rental or rental['status'] == 'destroyed_verified':
            return
        late = rental['status'] == 'created' and time.time() > rental['created_epoch']+2400
        if time.time() >= rental['deadline_epoch']-300 or late:
            try:
                finish(key, deadline=True)
                return
            except Exception as exc:
                print(vw.now(), key, 'deadline retry', type(exc).__name__, flush=True)
        time.sleep(30)


def inventory():
    print(json.dumps(registry(), ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'rent', 'watch', 'deploy', 'update', 'status',
                                           'finish', 'watchdog', 'inventory', 'verify'))
    parser.add_argument('--key')
    parser.add_argument('--seconds', type=int, default=14400)
    parser.add_argument('--timeout-minutes', type=int, default=40)
    parser.add_argument('--attempts', type=int, default=3)
    args = parser.parse_args(argv)
    MATRIX.mkdir(parents=True, exist_ok=True)
    if args.action == 'prepare':
        prepare()
    elif args.action == 'rent':
        rent(args.key)
    elif args.action == 'watch':
        return watch(args.key, args.timeout_minutes, args.attempts)
    elif args.action == 'deploy':
        deploy(args.key, args.seconds)
    elif args.action == 'update':
        update(args.key, args.seconds)
    elif args.action == 'status':
        status(args.key)
    elif args.action == 'finish':
        finish(args.key)
    elif args.action == 'watchdog':
        watchdog(args.key)
    elif args.action == 'inventory':
        inventory()
    elif args.action == 'verify':
        verify(args.key)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
