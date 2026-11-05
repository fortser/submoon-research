"""Одна аппаратная аренда по W2-D042; непрерывное исполнение без науки/повторов."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import vast_w2 as vw  # noqa: E402
import vast_p2 as vp  # noqa: E402

WORK = ROOT / 'results/evidence/W2_T054_hardware_9975wx_20261023'
PLAN = json.loads((WORK / 'approval.json').read_text(encoding='utf-8'))
LABEL = 'submoon-hw9975-20261023'
vw.WORK = WORK


def save(name, value):
    vw.save(WORK / name, value)


def inventory():
    return [{k: r.get(k) for k in ('id', 'machine_id', 'actual_status', 'label')}
            for r in vw.instances()]


def runtime(rental, finished=False):
    path = ROOT / 'tracking/runtime.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    rows = [r for r in value['processes'] if r.get('run_id') != PLAN['run_id']]
    if not finished:
        rows.append(dict(run_id=PLAN['run_id'], task_id=PLAN['task_id'], owner=PLAN['owner'],
                         kind='paid_hardware_trial', host='vast.ai', instance_id=rental['instance_id'],
                         status=rental['status'], approval_ref=PLAN['approval_ref'],
                         last_checked_utc=vw.now(), deadline_epoch=rental['deadline_epoch'],
                         max_budget_usd=PLAN['budget_usd'], controller_pid=os.getpid(),
                         manifest=(WORK / 'rental.json').relative_to(ROOT).as_posix(),
                         stop=f"vastai destroy instance {rental['instance_id']}",
                         note='Непрерывный операторский процесс; независимого watchdog нет.'))
    value.update(updated_utc=vw.now(), processes=rows)
    vw.save(path, value)


def preflight():
    if not PLAN['approved'] or PLAN['max_paid_attempts'] != 1:
        raise RuntimeError('Нет разрешения одной попытки')
    if (WORK / 'attempt.json').exists():
        raise RuntimeError('Повтор create запрещён')
    body = dict(machine_id={'eq': PLAN['machine_id']}, type='on-demand',
                rentable={'eq': True}, rented={'eq': False}, allocated_storage=60, limit=100)
    req = urllib.request.Request(vw.VAST_BUNDLES, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=30) as response:
        matches = [o for o in json.load(response).get('offers', []) if o['id'] == PLAN['offer_id']]
    if len(matches) != 1:
        raise RuntimeError('Согласованный оффер недоступен, замены нет')
    o = matches[0]
    valid = (o['machine_id'] == PLAN['machine_id'] and '9975WX' in o['cpu_name']
             and o['cpu_arch'] == 'amd64' and o['resource_type'] in ('gpu', 'cpu', 'compute')
             and o['verification'] in ('verified', 'unverified') and o['rentable'] and not o['rented']
             and o['cpu_cores_effective'] >= 64 and o['cpu_cores_effective'] >= .95 * o['cpu_cores']
             and o['cpu_ram'] >= 64000 and o['disk_space'] >= 60 and o['direct_port_count'] >= 1
             and o.get('reliability2', o.get('reliability', 0)) >= .97
             and 0 < o['dph_total'] <= PLAN['max_hourly_usd']
             and isinstance(o.get('storage_total_cost'), (int, float))
             and all(isinstance(o.get(k), (int, float)) and 0 <= o[k] <= .02
                     for k in ('inet_down_cost', 'inet_up_cost')))
    if not valid:
        raise RuntimeError('Оффер не прошёл аппаратные/финансовые предпосылки')
    offer = {k: o.get(k) for k in vw.SAFE_OFFER_FIELDS}
    save('offer.json', dict(utc=vw.now(), offer=offer))
    credit = vp._credit_usd()
    if credit is None or float(credit) < PLAN['budget_usd']:
        raise RuntimeError('Баланс не подтверждён')
    if not vw.CLI or not (Path.home() / '.ssh/vast_agent').is_file():
        raise RuntimeError('Нет CLI/SSH ключа')
    import paramiko
    before = inventory()
    save('instances_before.json', before)
    if any(r['label'] == LABEL for r in before):
        raise RuntimeError('Уже существует контракт с нашей label')
    save('preflight.json', dict(utc=vw.now(), passed=True, credit_usd=credit,
                               paramiko_version=paramiko.__version__, image=PLAN['image']))
    return offer


def rent(offer):
    started = time.time()
    save('attempt.json', dict(utc=vw.now(), started_epoch=started, offer_id=PLAN['offer_id'],
                             attempt_consumed=True, max_paid_attempts=1, max_http_create_requests=1))
    # CLI 0.3.1: один HTTP-запрос; локальный timeout, без правки установленного пакета.
    bootstrap = ('import vast\n'
                 'original = vast.http_request\n'
                 'def once(verb, args, *a, **k):\n'
                 '    args.retry = 1\n'
                 '    return original(verb, args, *a, **k)\n'
                 'vast.http_request = once\n'
                 'send = vast.requests.Session.send\n'
                 'def bounded(self, request, **kw):\n'
                 '    kw.setdefault("timeout", (15, 60))\n'
                 '    return send(self, request, **kw)\n'
                 'vast.requests.Session.send = bounded\n'
                 'vast.main()\n')
    command = [str(Path(vw.CLI).parent.parent / 'python.exe'), '-c', bootstrap,
               'create', 'instance', str(PLAN['offer_id']), '--image', PLAN['image'],
               '--disk', '60', '--ssh', '--direct', '--cancel-unavail', '--label', LABEL,
               '--onstart-cmd', 'touch /root/.no_auto_tmux', '--raw']
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
    env['PYTHONIOENCODING'] = 'utf-8'
    iid = None
    try:
        p = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=100, env=env)
        try:
            result, _ = json.JSONDecoder().raw_decode(p.stdout.strip())
        except ValueError:
            result = {}
        iid = result.get('new_contract') if isinstance(result, dict) else None
        if not isinstance(iid, int):
            # Не сохранять неструктурированный вывод: он может содержать секреты CLI.
            save('create_error.json', dict(utc=vw.now(), returncode=p.returncode,
                 reason='no_such_ask' if 'no_such_ask' in p.stdout + p.stderr else 'no_contract_in_response',
                 further_create_requests=False))
    except Exception as exc:
        save('create_error.json', dict(utc=vw.now(), error_type=type(exc).__name__,
                                      further_create_requests=False))
    if not isinstance(iid, int):
        # Даже неоднозначный ответ никогда не приводит к повторному платному запросу.
        for _ in range(3):
            rows = inventory()
            save('reconcile_instances.json', rows)
            found = [r for r in rows if r['label'] == LABEL]
            if len(found) == 1:
                iid = found[0]['id']
                break
            time.sleep(10)
    if not isinstance(iid, int):
        raise RuntimeError('Контракт не подтверждён; повтор create запрещён')
    rental = dict(instance_id=iid, status='created', created_epoch=started, created_utc=vw.now(),
                  deadline_epoch=started + 1800, offer=offer, approval_ref=PLAN['approval_ref'],
                  run_id=PLAN['run_id'], budget_usd=PLAN['budget_usd'], scientific_series_started=False)
    save('rental.json', rental)
    runtime(rental)
    print(json.dumps(dict(instance_id=iid, status='created', utc=vw.now())), flush=True)
    return rental


def connect(info):
    import paramiko
    url = urlparse(vw.cli('ssh-url', info['id']))
    endpoints = [(url.hostname, url.port)]
    if info.get('ssh_host') and info.get('ssh_port'):
        gateway = (info['ssh_host'], int(info['ssh_port']))
        if gateway not in endpoints:
            endpoints.append(gateway)
    for host, port in endpoints:
        client = paramiko.SSHClient()
        known = WORK / 'known_hosts'
        if known.exists():
            client.load_host_keys(str(known))
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, port=port, username='root', key_filename=str(Path.home() / '.ssh/vast_agent'),
                           timeout=15, banner_timeout=15, auth_timeout=15, allow_agent=False, look_for_keys=False)
            client.save_host_keys(str(known))
            return client
        except Exception:
            client.close()
    raise RuntimeError('SSH недоступен')


def probe(rental):
    until = rental['deadline_epoch'] - 600
    client = None
    while time.time() < until:
        info = vw.instance(rental['instance_id'])
        state = info.get('actual_status')
        save('instance_status.json', {k: info.get(k) for k in
                                     ('id', 'machine_id', 'actual_status', 'status_msg', 'dph_total')})
        print(vw.now(), 'instance', rental['instance_id'], state, flush=True)
        if info['machine_id'] != PLAN['machine_id']:
            raise RuntimeError('Неверная машина')
        if state in ('exited', 'offline', 'unknown'):
            raise RuntimeError('Отказ provisioning')
        if state == 'running':
            try:
                client = connect(info)
                break
            except Exception as exc:
                save('last_ssh_error.json', dict(utc=vw.now(), error_type=type(exc).__name__))
        time.sleep(15)
    if client is None:
        raise RuntimeError('Истёк срок ожидания')
    rental.update(status='running', last_checked_utc=vw.now())
    save('rental.json', rental)
    runtime(rental)
    remote = '/workspace/' + PLAN['run_id']
    try:
        # Исправление auto-tmux только в собственном одноразовом контейнере.
        channel = client.invoke_shell()
        time.sleep(2)
        channel.send('touch /root/.no_auto_tmux; echo AUTO_TMUX_DISABLED\n')
        time.sleep(2)
        channel.close()
        listing = vw.remote_command(client, 'ls -la /workspace; ps -eo pid,comm,pcpu,pmem; test ! -e '
                                    + shlex.quote(remote), timeout=30)
        (WORK / 'remote_before.txt').write_text(listing, encoding='utf-8')
        vw.remote_command(client, 'mkdir ' + shlex.quote(remote), timeout=30)
        with client.open_sftp() as sftp:
            sha = vw.put_if_missing_or_identical(client, sftp, WORK / 'hardware_probe.py',
                                                remote + '/hardware_probe.py')
        error = None
        try:
            vw.remote_command(client, f'timeout 240s python3 {remote}/hardware_probe.py > '
                              f'{remote}/hardware.json 2> {remote}/stderr.txt', timeout=270)
        except Exception as exc:
            error = type(exc).__name__
        with client.open_sftp() as sftp:
            for name in ('hardware.json', 'stderr.txt'):
                sftp.get(remote + '/' + name, str(WORK / name))
        expected = vw.remote_command(client, 'sha256sum ' + remote + '/hardware.json').split()[0]
        if expected != vw.digest(WORK / 'hardware.json'):
            raise RuntimeError('Не совпал SHA выгрузки')
        save('transfer.json', dict(utc=vw.now(), probe_sha256=sha, hardware_sha256=expected,
                                  command_error=error))
        if error:
            raise RuntimeError('Нагрузочная проверка не завершена')
        hardware = json.loads((WORK / 'hardware.json').read_text(encoding='utf-8'))
        cgroup = hardware['cgroup']['cpu.max']
        quota = None if not cgroup or cgroup.startswith('max') else int(cgroup.split()[0])/int(cgroup.split()[1])
        summary = dict(utc=vw.now(), instance_id=rental['instance_id'],
                       affinity_vcpus=len(hardware['affinity']), physical_cores=len(hardware['physical_groups']),
                       quota_vcpus=quota, memory_limit_bytes=hardware['cgroup']['memory.max'],
                       memory_probe_passed=hardware['memory_probe']['passed'],
                       disk_probe_passed=hardware['disk_probe']['passed'])
        for key in ('single_core', 'physical_core_load', 'logical_core_load'):
            v = hardware[key]
            summary[key] = {k: v[k] for k in ('workers', 'wall_seconds', 'sum_cpu_seconds', 'iterations_per_second')}
            summary[key]['equivalent_busy_cpus'] = v['sum_cpu_seconds']/v['wall_seconds']
        save('summary.json', summary)
        rental.update(status='hardware_test_completed', last_checked_utc=vw.now())
        save('rental.json', rental)
        runtime(rental)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    finally:
        client.close()


def finish(rental):
    rows = inventory()
    save('instances_before_destroy.json', rows)
    matches = [r for r in rows if r['id'] == rental['instance_id']]
    if matches:
        if len(matches) != 1 or matches[0]['label'] != LABEL or matches[0]['machine_id'] != PLAN['machine_id']:
            raise RuntimeError('Нельзя подтвердить принадлежность удаляемого инстанса')
        result = vw.cli('destroy', 'instance', rental['instance_id'])
        save('destroy_response.json', {k: result.get(k) for k in ('success', 'msg')})
    for _ in range(5):
        after = inventory()
        save('instances_after_destroy.json', after)
        if not any(r['id'] == rental['instance_id'] for r in after):
            break
        time.sleep(5)
    else:
        raise RuntimeError('Удаление не подтверждено: нужен оператор')
    elapsed = time.time() - rental['created_epoch']
    rental.update(status='destroyed_verified', finished_utc=vw.now(), elapsed_seconds=elapsed,
                  compute_disk_cost_estimate_usd=elapsed/3600*rental['offer']['dph_total'],
                  cost_is_estimate_not_invoice=True)
    save('rental.json', rental)
    runtime(rental, finished=True)
    save('sha256.json', {p.name: vw.digest(p) for p in WORK.iterdir()
                         if p.is_file() and p.name != 'sha256.json'})
    print(json.dumps(dict(status=rental['status'], instance_id=rental['instance_id'],
                         elapsed_seconds=elapsed, estimated_usd=rental['compute_disk_cost_estimate_usd'])), flush=True)


def main():
    offer = preflight()
    if len(sys.argv) > 1 and sys.argv[1] == 'preflight':
        print(json.dumps(dict(preflight='passed', offer=offer), ensure_ascii=False), flush=True)
        return
    rental = rent(offer)
    try:
        probe(rental)
    except Exception as exc:
        save('probe_error.json', dict(utc=vw.now(), error_type=type(exc).__name__,
                                     message=str(exc)[:300], scientific_series_started=False))
        raise
    finally:
        finish(rental)


if __name__ == '__main__':
    main()
