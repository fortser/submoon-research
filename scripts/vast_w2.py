"""Выделенный транспорт W2/Vast.ai: очищенные ответы, SHA, дедлайн и выгрузка.

Запуск глобальным Python с установленными vastai/paramiko. Ключи не копируются
на сервер и не входят в scientific manifest. Rent требует отдельного approval JSON.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT/'scratch/w2_remote'
CLI = shutil.which('vastai')
VAST_BUNDLES = 'https://console.vast.ai/api/v0/bundles/'
SAFE_OFFER_FIELDS = ('id', 'machine_id', 'cpu_name', 'cpu_cores_effective', 'cpu_cores',
    'cpu_ram', 'cpu_arch', 'gpu_name', 'num_gpus', 'dph_total', 'dph_base', 'storage_total_cost',
    'storage_cost', 'inet_down_cost', 'inet_up_cost', 'reliability', 'verification', 'disk_space',
    'geolocation', 'direct_port_count', 'rentable', 'rented', 'resource_type',
    'internet_down_cost_per_tb', 'internet_up_cost_per_tb')

CPU_PATTERN = re.compile(r'EPYC\s+(?:9575F|9475F|9375F|9474F|9374F|9274F|9174F|75F3|73F3|7543|7443|7343|4\d{3}[A-Z]*|9\d{2}[45][A-Z]*)\b', re.I)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def cli(*arguments):
    env = {k: v for k, v in os.environ.items() if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
    env['PYTHONIOENCODING'] = 'utf-8'
    command = [CLI, *map(str, arguments), '--raw']
    # Vast API бывает медленным; повторяем только идемпотентные чтения, не create.
    safe = bool(arguments) and str(arguments[0]) in ('show', 'ssh-url')
    attempts = 3 if safe else 1
    result = None
    for attempt in range(attempts):
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    encoding='utf-8', timeout=120, env=env)
            break
        except subprocess.TimeoutExpired:
            if attempt == attempts-1:
                raise
            time.sleep(5)
    if result.returncode:
        raise RuntimeError('Vast CLI failed: '+str(result.returncode))
    text = result.stdout.strip()
    if text.startswith('ssh://'):
        return text.splitlines()[0]
    try:
        value, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        import ast
        try:
            value = ast.literal_eval(text)
        except Exception:
            raise RuntimeError('Vast CLI вернул неструктурированный ответ') from None
    if isinstance(value, dict) and (value.get('success') is False or 'error' in value):
        raise RuntimeError('Vast API отказал; тип ошибки: '+str(value.get('error', 'unsuccessful'))[:100])
    return value


def anonymous_offers(gpu=False, max_hourly_usd=None, machine_ids=None):
    """Прямой POST к публичному endpoint без ключа: не тратит дневную квоту аккаунта."""
    hourly = max_hourly_usd if max_hourly_usd is not None else (0.12 if gpu else 0.02)
    body = {'rentable': {'eq': True}, 'rented': {'eq': False}, 'type': 'on-demand',
            'allocated_storage': 20, 'order': [['dph_total', 'asc']], 'limit': 1000,
            'cpu_arch': {'eq': 'amd64'}, 'disk_space': {'gte': 20},
            'cpu_cores_effective': {'gte': 16}, 'dph_total': {'lte': hourly}}
    if gpu:
        body.update(num_gpus={'gte': 1}, cpu_ram={'gte': 30000}, direct_port_count={'gte': 1},
                    inet_down_cost={'lte': 0.02}, inet_up_cost={'lte': 0.02}, reliability={'gte': 0.98})
    else:
        body['num_gpus'] = {'eq': 0}
    if machine_ids:
        body['machine_id'] = {'in': [int(x) for x in machine_ids]}
    request = urllib.request.Request(VAST_BUNDLES, data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'}, method='POST')
    opened = urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=120)
    with opened as response:
        data = json.loads(response.read().decode('utf-8', 'replace'))
    return data.get('offers', []) if isinstance(data, dict) else []


def offers(gpu=False, max_hourly_usd=None, offer_ids=None, machine_ids=None):
    hourly = max_hourly_usd if max_hourly_usd is not None else (0.12 if gpu else 0.02)
    query = ('cpu_cores_effective>=16 disk_space>=20 reliability>=0.98 '
             'rentable=true rented=false cpu_arch=amd64 '
             + (f'num_gpus>=1 cpu_ram>=30 direct_port_count>=1 dph_total<={hourly} '
                'inet_down_cost<=0.02 inet_up_cost<=0.02' if gpu else 'num_gpus=0 dph_total<=0.02'))
    if machine_ids:
        query = f"machine_id in [{','.join(map(str, machine_ids))}] " + query
    try:
        # Предпочтительный путь: публичный endpoint без ключа, квота аккаунта не тратится.
        result = anonymous_offers(gpu=gpu, max_hourly_usd=hourly, machine_ids=machine_ids)
        source = 'anonymous_direct'
    except Exception:
        # Резервный путь через CLI; расходует дневную поисковую квоту аккаунта.
        result = cli('search', 'offers', query, '--type', 'on-demand', '--storage', 20,
                     '--limit', max(200, len(offer_ids or [])), '--order', 'dph_total')
        source = 'cli'
    clean = [{k: r.get(k) for k in SAFE_OFFER_FIELDS} for r in result if isinstance(r, dict)]
    clean = [o for o in clean if o.get('id') is not None]
    save(WORK/'offers_classification.json', dict(utc=now(), source=source, all_offers=clean))
    clean = [o for o in clean if (True if gpu else CPU_PATTERN.search(o['cpu_name'] or ''))]
    # num_gpus=0 включает дисковые контракты! Имя CPU не даёт права вычислений.
    clean = [o for o in clean if o['resource_type'] in (('gpu',) if gpu else ('cpu', 'compute'))]
    if offer_ids:
        wanted = {int(x) for x in offer_ids}
        clean = [o for o in clean if o['id'] in wanted]
    if machine_ids:
        allowed_machines = {int(x) for x in machine_ids}
        clean = [o for o in clean if o['machine_id'] in allowed_machines]
    clean = [o for o in clean if isinstance(o.get('dph_total'), (int, float)) and o['dph_total'] <= hourly]
    save(WORK/'offers.json', dict(utc=now(), source=source, query=query, offers=clean))
    print(json.dumps(clean, ensure_ascii=False), flush=True)
    return clean


def cpu_monitor(minutes=20):
    started = time.monotonic()
    history = WORK/'cpu_monitor.jsonl'
    while True:
        try:
            current = offers()
            snapshot = dict(utc=now(), elapsed_seconds=time.monotonic()-started, offers=current)
        except Exception as exc:
            snapshot = dict(utc=now(), elapsed_seconds=time.monotonic()-started, error=type(exc).__name__)
        with history.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(snapshot, ensure_ascii=False)+'\n')
        if time.monotonic()-started >= minutes*60:
            return
        time.sleep(min(45, max(0, minutes*60-(time.monotonic()-started))))


def package():
    WORK.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in ('src', 'scripts', 'tests', 'configs', 'docs', 'tracking', 'results/evidence',
                 'references', 'reports', 'arXiv-2609.03564v1', 'data/raw', 'data/processed',
                 'data/interim', 'data/kernels', 'runs'):
        paths += [p for p in (ROOT/name).rglob('*') if p.is_file()
                  and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pem', '.key')
                  and p.name != '.write.lock']
    paths += [p for p in ROOT.iterdir() if p.is_file() and p.suffix in ('.md', '.toml')]
    paths += [ROOT/'requirements-w2.txt', ROOT/'math.txt', ROOT/'good_cpu.txt']
    paths += list((ROOT/'VastAI_logs').glob('*.md'))
    paths = sorted(set(paths))
    index = {p.relative_to(ROOT).as_posix(): digest(p) for p in paths}
    save(WORK/'bundle_manifest.json', dict(created_utc=now(), files=index))
    with tarfile.open(WORK/'bundle.tar.gz', 'w:gz', compresslevel=1) as tar:
        for p in paths:
            tar.add(p, arcname=p.relative_to(ROOT).as_posix(), recursive=False)
        tar.add(WORK/'bundle_manifest.json', arcname='bundle_manifest.json')
    out = dict(bytes=(WORK/'bundle.tar.gz').stat().st_size, sha256=digest(WORK/'bundle.tar.gz'), files=len(paths))
    save(WORK/'bundle_info.json', out)
    print(json.dumps(out))


def instances():
    values = cli('show', 'instances')
    return values if isinstance(values, list) else values.get('instances', [])


def instance(instance_id):
    value = cli('show', 'instance', int(instance_id))
    if isinstance(value, dict) and int(value.get('id', -1)) == int(instance_id):
        return value
    if isinstance(value, list):
        matches = [x for x in value if int(x.get('id', -1)) == int(instance_id)]
        if len(matches) == 1:
            return matches[0]
    raise RuntimeError('Согласованный instance не найден')


def connection(info):
    import paramiko
    known = WORK/'known_hosts'
    key = Path.home()/'.ssh/vast_agent'
    endpoints = []
    endpoint = cli('ssh-url', info['id'])
    parsed = urlparse(endpoint)
    if parsed.scheme == 'ssh' and parsed.hostname and parsed.port:
        endpoints.append((parsed.hostname, parsed.port))
    # Прямой IP из ssh-url может не открываться снаружи; шлюз ssh_host/ssh_port
    # Vast проксирует тот же контейнер. Пробуем оба, свежий клиент на попытку.
    if info.get('ssh_host') and info.get('ssh_port'):
        gateway = (str(info['ssh_host']), int(info['ssh_port']))
        if gateway not in endpoints:
            endpoints.append(gateway)
    if not endpoints:
        raise RuntimeError('CLI не вернул проверяемый SSH endpoint')
    error = None
    for host, port in endpoints:
        for _ in range(2):
            client = paramiko.SSHClient()
            if known.exists():
                client.load_host_keys(str(known))
            # TOFU применяется только к endpoint, возвращённому авторизованным API Vast.
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(host, port=port, username='root', key_filename=str(key),
                               timeout=20, auth_timeout=20, banner_timeout=20,
                               allow_agent=False, look_for_keys=False)
            except Exception as exc:
                error = exc
                client.close()
                time.sleep(2)
                continue
            client.save_host_keys(str(known))
            return client
    raise RuntimeError('SSH не подключился: '+type(error).__name__) from None


def remote_command(client, command, timeout=120):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output, errors = stdout.read().decode('utf-8', errors='replace'), stderr.read().decode('utf-8', errors='replace')
    code = stdout.channel.recv_exit_status()
    if code:
        raise RuntimeError(f'Remote exit {code}: '+(output+errors)[-3000:])
    return output


def put_if_missing_or_identical(client, sftp, source, destination):
    expected = digest(source)
    try:
        sftp.stat(destination)
    except OSError:
        sftp.put(str(source), destination)
    else:
        actual = remote_command(client, 'sha256sum '+destination).split()[0]
        if actual != expected:
            raise RuntimeError('Remote path exists with different SHA; refusing overwrite: '+destination)
        return expected
    actual = remote_command(client, 'sha256sum '+destination).split()[0]
    if actual != expected:
        raise RuntimeError('SHA mismatch after upload: '+destination)
    return expected


def local_identity():
    import platform
    return dict(host=platform.node(), cwd=str(ROOT))


def verify():
    """Подтверждает, что команды идут на арендованном Linux-сервере с нашим bundle."""
    import platform
    rental = json.loads((WORK/'rental.json').read_text(encoding='utf-8'))
    iid = int(rental['instance_id'])
    expected_manifest = digest(WORK/'bundle_manifest.json')
    info = instance(iid)
    with connection(info) as client:
        remote = remote_command(client,
            'echo HOST=$(hostname); echo OS=$(uname -s); echo ARCH=$(uname -m); '
            'echo ID=$(cat /workspace/.w2_instance_id 2>/dev/null); '
            'echo MANIFEST=$(sha256sum /workspace/submoon-research/bundle_manifest.json 2>/dev/null | cut -d" " -f1); '
            'echo IP=$(hostname -I 2>/dev/null | cut -d" " -f1)', timeout=30)
    values = dict(line.split('=', 1) for line in remote.strip().splitlines() if '=' in line)
    local_host = platform.node()
    print('LOCAL  host=%s cwd=%s' % (local_host, ROOT))
    print('REMOTE host=%s os=%s arch=%s ip=%s sentinel_id=%s manifest=%s' % (
        values.get('HOST'), values.get('OS'), values.get('ARCH'), values.get('IP'),
        values.get('ID'), values.get('MANIFEST')))
    problems = []
    if values.get('OS') != 'Linux':
        problems.append('remote OS is not Linux')
    if values.get('ID') != str(iid):
        problems.append('instance sentinel mismatch')
    if values.get('MANIFEST') != expected_manifest:
        problems.append('remote bundle manifest SHA mismatch')
    if values.get('HOST', '').lower() == local_host.lower():
        problems.append('remote host equals local host')
    if problems:
        raise RuntimeError('Remote verification failed: '+'; '.join(problems))
    print('REMOTE VERIFIED: instance %d runs exactly the local bundle %s' % (iid, expected_manifest))
    return values


def rent(approval_path):
    approval = json.loads(Path(approval_path).read_text(encoding='utf-8'))
    if approval.get('approved') is not True or not approval.get('user_message'):
        raise ValueError('Нет явного пользовательского разрешения')
    price_limit = .20 if approval.get('gpu_fallback') else .02
    if (approval['budget_usd'] > 3 or approval['max_hours'] > 6
            or approval['max_instances'] != 1 or approval['max_hourly_usd'] > price_limit):
        raise ValueError('План превышает подготовленные пределы')
    if (WORK/'rental.json').exists():
        raise ValueError('Есть запись аренды: новую автоматически не создавать')
    approved_ids = approval.get('offer_ids', [])
    available = offers(gpu=bool(approval.get('gpu_fallback')),
                       max_hourly_usd=approval['max_hourly_usd'], offer_ids=approved_ids,
                       machine_ids=approval.get('machine_ids'))
    allowed = [o for o in available if o['id'] in approved_ids]
    allowed = [o for o in allowed if o['dph_total'] <= approval['max_hourly_usd']
               and o['cpu_cores_effective'] >= 16
               and (o['resource_type'] == 'gpu' and o['num_gpus'] >= 1 and o['cpu_ram'] >= 30000
                    if approval.get('gpu_fallback') else o['resource_type'] in ('cpu', 'compute') and o['num_gpus'] == 0)
               and (o['verification'] == 'verified' or approval.get('allow_unverified'))]
    if approved_ids and len(allowed) != 1:
        raise RuntimeError('Точный offer_id не найден или неоднозначен')
    if not allowed:
        raise RuntimeError('Нет оффера в согласованных границах')
    # Предпочтение современному AMD CPU при близкой цене; это не оценка быстродействия.
    def preference(o):
        model = o['cpu_name']
        generation = (0 if re.search(r'\b9\d{2}5|\b4\d{3}', model) else
                      1 if re.search(r'\b9\d{2}4', model) else
                      2 if re.search(r'\b7\d{2}3|\b7\dF3', model) else 3)
        return generation, o['dph_total']
    allowed.sort(key=preference)
    offer = allowed[0]
    result = cli('create', 'instance', offer['id'], '--image', 'ubuntu:22.04',
        '--disk', 20, '--ssh', '--direct', '--cancel-unavail', '--label', 'submoon-W2-three-engines')
    iid = result.get('new_contract')
    if not result.get('success') or not isinstance(iid, int):
        raise RuntimeError('Не подтверждён create instance')
    # Не сохранять result: он может содержать instance_api_key.
    record = dict(instance_id=iid, created_utc=now(), created_epoch=time.time(),
        deadline_epoch=time.time()+approval['max_hours']*3600, approval=approval, offer=offer,
        status='created', uploaded_bytes=0, downloaded_bytes=0)
    save(WORK/'rental.json', record)
    # Надзор запускается сразу после create, до развёртывания.
    with (WORK/'watchdog.log').open('a', encoding='utf-8') as log:
        watch = subprocess.Popen([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), 'watchdog'],
            cwd=ROOT, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    record['watchdog_pid'] = watch.pid
    save(WORK/'rental.json', record)
    print(json.dumps(dict(instance_id=iid, hourly=offer['dph_total'], effective_cpus=offer['cpu_cores_effective'])))


def deploy():
    rental = json.loads((WORK/'rental.json').read_text(encoding='utf-8'))
    if rental.get('status') not in ('created', 'deployed'):
        raise RuntimeError('Состояние аренды не допускает deploy/redeploy')
    info = instance(rental['instance_id'])
    if info.get('actual_status') != 'running':
        print(json.dumps(dict(status=info.get('actual_status'))))
        return
    client = connection(info)
    try:
        sftp = client.open_sftp()
        project_root = '/workspace/submoon-research'
        try:
            sftp.stat(project_root)
            workspace_exists = True
        except OSError:
            workspace_exists = False
        if workspace_exists:
            expected_manifest = digest(WORK/'bundle_manifest.json')
            actual_manifest = remote_command(
                client, 'sha256sum '+project_root+'/bundle_manifest.json'
            ).split()[0]
            if actual_manifest != expected_manifest:
                raise RuntimeError(
                    'Remote project exists with a different manifest; no upload or extraction performed'
                )
            check = remote_command(
                client,
                'cd '+project_root+' && W2_REMOTE_EXECUTION=1 '
                '.venv/bin/python scripts/w2_remote_preflight.py', timeout=120
            )
            report = json.loads(check.splitlines()[-1])
            if not all(report['checks'].values()):
                raise RuntimeError('Existing remote project failed manifest preflight; not overwriting')
        else:
            archive = '/workspace/w2_bundle_'+digest(WORK/'bundle.tar.gz')+'.tar.gz'
            # Базовый образ ubuntu:22.04 не содержит /workspace.
            remote_command(client, 'mkdir -p /workspace')
            put_if_missing_or_identical(client, sftp, WORK/'bundle.tar.gz', archive)
            remote_command(client, 'mkdir -p '+project_root)
            remote_command(client, 'tar -xzf '+archive+' -C '+project_root)
            expected_manifest = digest(WORK/'bundle_manifest.json')
            actual_manifest = remote_command(
                client, 'sha256sum '+project_root+'/bundle_manifest.json'
            ).split()[0]
            if actual_manifest != expected_manifest:
                raise RuntimeError('SHA of extracted bundle manifest does not match')
        remote_command(client, 'echo %d > /workspace/.w2_instance_id && hostname > /workspace/.w2_hostname'
                       % int(rental['instance_id']))
        effective = min(16, int(rental['offer']['cpu_cores_effective']))
        # Команда фиксированная, входной offer не подставляется как shell-код.
        script = ('#!/bin/bash\nset -euo pipefail\nexport DEBIAN_FRONTEND=noninteractive\n'
            'apt-get update -qq\napt-get install -y -qq python3 python3-venv python3-dev build-essential git\n'
            'cd /workspace/submoon-research\npython3 -m venv .venv\n'
            '.venv/bin/python -m pip install --disable-pip-version-check -r requirements-w2.txt\n'
            '.venv/bin/python -m pip install --no-deps -e .\n'
            'export W2_REMOTE_EXECUTION=1\n'
            f'export W2_INSTANCE_ID={int(rental["instance_id"])}\n'
            f'export W2_EFFECTIVE_CPUS={effective}\n'
            'export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMBA_NUM_THREADS=1\n'
            'echo "=== GATE preflight ==="\n'
            '.venv/bin/python scripts/w2_remote_preflight.py\n'
            # Полный pytest оставлен обязательным гейтом; после E1 (data/interim,
            # good_cpu.txt) он должен быть полностью зелёным, включая движок B.
            'echo "=== GATE pytest (mandatory, full) ==="\n'
            '.venv/bin/python -m pytest\n'
            'echo "=== GATE ruff ==="\n'
            '.venv/bin/python -m ruff check src scripts tests\n'
            'echo "=== GATE validate ==="\n'
            '.venv/bin/python scripts/project.py validate\n'
            'echo "=== GATE smoke ==="\n'
            '.venv/bin/python scripts/project.py smoke\n'
            'echo "=== SCIENCE campaign ==="\n'
            '.venv/bin/python -u scripts/run_w2_comparison.py campaign --workers 16 --seconds 3600\n')
        start_file = WORK/('w2_start_'+str(rental['instance_id'])+'.sh')
        # Явный LF: Windows write_text иначе добавит CRLF и bash отвергнет pipefail.
        start_file.write_text(script, encoding='utf-8', newline='\n')
        remote_start = '/workspace/w2_start_'+str(rental['instance_id'])+'.sh'
        remote_log = '/workspace/w2_execution_'+str(rental['instance_id'])+'.log'
        if remote_command(client, 'test ! -e '+remote_log+' && echo absent').strip() != 'absent':
            raise RuntimeError('Run-specific remote log already exists; refusing overwrite')
        if rental.get('status') == 'deployed':
            active = remote_command(client, "pgrep -af '[w]2_start_"+str(rental['instance_id'])+
                ".sh|[/]workspace/submoon-research/.venv/bin/python' || true")
            if active.strip():
                raise RuntimeError('На сервере есть процесс W2 для этого проекта; процессы не трогаю')
        put_if_missing_or_identical(client, sftp, start_file, remote_start)
        result = remote_command(client, 'nohup bash '+remote_start+' > '+remote_log+' 2>&1 < /dev/null & echo $!')
        rental.update(status='deployed', remote_pid=int(result.strip()), uploaded_bytes=(WORK/'bundle.tar.gz').stat().st_size,
                      remote_log_path=remote_log, deployed_utc=now())
        save(WORK/'rental.json', rental)
        print(json.dumps(dict(instance_id=rental['instance_id'], remote_pid=rental['remote_pid'])))
    finally:
        client.close()


def status():
    rental = json.loads((WORK/'rental.json').read_text(encoding='utf-8'))
    info = instance(rental['instance_id'])
    clean = {k: info.get(k) for k in ('id', 'actual_status', 'cpu_cores_effective', 'cpu_ram', 'dph_total', 'disk_space')}
    clean['elapsed_hours'] = (time.time()-rental['created_epoch'])/3600
    clean['conservative_compute_storage_usd'] = clean['elapsed_hours']*rental['offer']['dph_total']
    if info.get('actual_status') == 'running':
        try:
            with connection(info) as client:
                log_path = rental.get('remote_log_path', '/workspace/w2_execution.log')
                clean['log_tail'] = remote_command(client, 'tail -n 18 '+log_path, timeout=20)
                clean['workers'] = remote_command(client, "ps -eo comm= | sort | uniq -c | tail -n 12", timeout=20)
        except Exception as exc:
            clean['ssh_status'] = type(exc).__name__
    print(json.dumps(clean, ensure_ascii=False))


def download(client, rental):
    log_path = rental.get('remote_log_path', '/workspace/w2_execution.log')
    script = '''import pathlib, tarfile, hashlib, json
root=pathlib.Path('/workspace/submoon-research')
folders=[p for p in (root/'runs').iterdir() if p.name.startswith('W2-') and p.stat().st_mtime >= START]
files=[p for d in folders for p in d.rglob('*') if p.is_file()]
hashes={p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
pathlib.Path('/workspace/w2_results_manifest.json').write_text(json.dumps(hashes))
with tarfile.open('/workspace/w2_results.tar.gz','w:gz',compresslevel=1) as tar:
 for p in files: tar.add(p,arcname=p.relative_to(root).as_posix())
 tar.add('/workspace/w2_results_manifest.json',arcname='w2_results_manifest.json')
 log=pathlib.Path('__LOG__')
 if log.exists(): tar.add(str(log),arcname='w2_execution.log')
print(len(files))
'''.replace('START', str(rental['created_epoch']-5)).replace('__LOG__', log_path)
    sftp = client.open_sftp()
    with sftp.open('/workspace/w2_pack_results.py', 'w') as f:
        f.write(script)
    sftp.close()
    remote_command(client, 'python3 /workspace/w2_pack_results.py', timeout=90)
    sftp = client.open_sftp()
    target = WORK/'results.tar.gz'
    sftp.get('/workspace/w2_results.tar.gz', str(target))
    sftp.close()
    remote_hash = remote_command(client, 'sha256sum /workspace/w2_results.tar.gz').split()[0]
    if digest(target) != remote_hash:
        raise RuntimeError('SHA выгруженного архива не совпал')
    with tarfile.open(target, 'r:gz') as tar:
        entries = json.load(tar.extractfile('w2_results_manifest.json'))
        for name, expected in entries.items():
            destination = (ROOT/name).resolve()
            if not destination.is_relative_to((ROOT/'runs').resolve()) or not name.startswith('runs/W2-'):
                raise ValueError('Путь результата вне разрешённой области')
            data = tar.extractfile(name).read()
            if hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Повреждён результат '+name)
            if destination.exists():
                if digest(destination) != expected:
                    raise ValueError('Попытка перезаписи существующего run '+name)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
        try:
            (WORK/'execution.log').write_bytes(tar.extractfile('w2_execution.log').read())
        except KeyError:
            pass
    rental.update(downloaded_bytes=target.stat().st_size, result_archive_sha256=remote_hash,
                  verified_result_files=len(entries), downloaded_utc=now())
    save(WORK/'rental.json', rental)
    return len(entries)


def finish(deadline=False):
    rental = json.loads((WORK/'rental.json').read_text(encoding='utf-8'))
    iid = rental['instance_id']
    found = [x for x in instances() if x['id'] == iid]
    if not found:
        rental['status'] = 'destroyed_verified'
        save(WORK/'rental.json', rental)
        return
    if not deadline:
        with connection(found[0]) as client:
            download(client, rental)
    else:
        try:
            with connection(found[0]) as client:
                remote_command(client, "pkill -f '[r]un_w2_comparison.py' || true", timeout=15)
                download(client, rental)
        except Exception as exc:
            rental['deadline_download_error'] = type(exc).__name__
    cli('destroy', 'instance', iid)
    if any(x['id'] == iid for x in instances()):
        raise RuntimeError('Удаление инстанса пока не подтверждено')
    rental.update(status='destroyed_verified', destroyed_utc=now(),
        elapsed_hours=(time.time()-rental['created_epoch'])/3600)
    rental['compute_storage_upper_estimate_usd'] = rental['elapsed_hours']*rental['offer']['dph_total']
    save(WORK/'rental.json', rental)
    print(json.dumps({k: rental[k] for k in ('instance_id', 'status', 'elapsed_hours', 'compute_storage_upper_estimate_usd')}))


def watchdog():
    while True:
        rental = json.loads((WORK/'rental.json').read_text(encoding='utf-8'))
        if rental['status'] == 'destroyed_verified':
            return
        # Резерв 5 минут на выгрузку и API. Stop не завершает оплату хранения.
        late_deploy = rental['status'] == 'created' and time.time() > rental['created_epoch']+1800
        if time.time() >= rental['deadline_epoch']-300 or late_deploy:
            try:
                finish(deadline=True)
                return
            except Exception as exc:
                print(now(), 'deadline cleanup retry', type(exc).__name__, flush=True)
        time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('offers', 'cpu_monitor', 'package', 'rent', 'deploy', 'status', 'finish', 'watchdog', 'inventory', 'verify'))
    parser.add_argument('--approval')
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    if args.action == 'rent':
        rent(args.approval)
    elif args.action == 'inventory':
        print(json.dumps([dict(id=x['id'], status=x.get('actual_status'), label=x.get('label')) for x in instances()]))
    else:
        globals()[args.action]()


if __name__ == '__main__':
    main()
