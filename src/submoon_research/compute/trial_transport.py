"""Изолированный REST/SSH транспорт аппаратного теста; без CLI create/retry.

Authorization — только заголовок, не URL/логи. Ошибки наружу содержат только
контролируемый код/HTTP status, никогда произвольный traceback/тело ответа.
"""
import hashlib
import importlib.metadata
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import shlex
import shutil
import time

from submoon_research.compute.hardware_trial import (
    IMAGE, TRANSFER_BYTES, TrialError, digest, load, safe_receipt, save,
)

BASE = 'https://console.vast.ai/api/v0'
MAX_RESPONSE = 2_000_000
MAX_HARDWARE = 1_000_000


class TransportError(TrialError):
    def __init__(self, code, status=None):
        self.status = status
        super().__init__(code)


def _worker(pipe, fn, args):
    try:
        pipe.send(('ok', fn(*args)))
    except BaseException as exc:
        # Ни traceback, ни repr чужого исключения не покидают worker.
        code = str(exc) if isinstance(exc, TrialError) else 'transport_error'
        pipe.send(('error', (code, getattr(exc, 'status', None))))
    finally:
        pipe.close()


def bounded(fn, args, timeout):
    """Hard wall timeout отдельного процесса; create всё равно вызывается один раз.

    При убийстве worker после принятия запроса ответ неоднозначен: reconciliation,
    а не повтор. Нельзя заменить это timeout Future с живым сетевым потоком.
    """
    ctx = multiprocessing.get_context('spawn')
    reader, writer = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_worker, args=(writer, fn, args), daemon=True)
    try:
        process.start()
        writer.close()
        if not reader.poll(timeout):
            raise TransportError('transport_error')
        try:
            status, result = reader.recv()
        except EOFError:
            raise TransportError('transport_error') from None
        if status != 'ok':
            raise TransportError(*result)
        return result
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join(2)
            process.close()
        reader.close()
        writer.close()


def _http(key, method, route, body, timeout, params):
    return VastAPI(key=key)._request_direct(method, route, body, timeout, params)


def _ssh(source, key_path, info, folder, run_id, timeout):
    return SSHProbe(source, key_path)._run(info, folder, run_id, timeout)


def credential():
    key = os.environ.get('VAST_API_KEY', '').strip()
    if key:
        return key
    for path in (Path.home() / '.config/vastai/vast_api_key', Path.home() / '.vast_api_key'):
        if path.is_file():
            value = path.read_text(encoding='utf-8').strip()
            return value.split('=', 1)[1].strip() if '=' in value else value
    raise TrialError('credential_missing')


class VastAPI:
    def __init__(self, key=None, request=None):
        # Секрет не включается в __repr__, manifests или CLI arguments.
        self._key = credential() if key is None else key
        self._request = request

    def request(self, method, route, body=None, timeout=20, params=None):
        if self._request is not None:
            # Только инъекция офлайн-тестов; реальный HTTP всегда в bounded worker.
            return self._request_direct(method, route, body, timeout, params)
        return bounded(_http, (self._key, method, route, body, timeout, params), timeout)

    def _request_direct(self, method, route, body=None, timeout=20, params=None):
        import requests
        # Сессия на запрос: preflight параллелен; общий Session не нужен.
        with requests.Session() as session:
            session.trust_env = False
            call = self._request or session.request
            try:
                response = call(method, BASE + route, json=body, params=params,
                                headers={'Authorization': 'Bearer ' + self._key, 'Accept': 'application/json'},
                                timeout=(min(10, timeout), timeout), allow_redirects=False, stream=True)
                try:
                    content = bytearray()
                    for chunk in response.iter_content(65536):
                        content.extend(chunk)
                        if len(content) > MAX_RESPONSE:
                            raise TransportError('response_too_large', response.status_code)
                    try:
                        value = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise TransportError('invalid_json', response.status_code) from None
                    if not 200 <= response.status_code < 300:
                        # Только whitelist безопасных кодов, без detail/message от сервера.
                        code = value.get('error') if isinstance(value, dict) else None
                        if code not in ('no_such_ask', 'unavailable', 'insufficient_credit'):
                            code = 'http_error'
                        raise TransportError(code, response.status_code)
                    return response.status_code, value
                finally:
                    response.close()
            except TransportError:
                raise
            except Exception:
                raise TransportError('transport_error') from None

    def offer(self, offer_id, machine_id, timeout):
        _, data = self.request('POST', '/bundles/', dict(machine_id={'eq': machine_id},
                               type='on-demand', allocated_storage=60, limit=100), timeout)
        matches = [r for r in data.get('offers', []) if r.get('id') == offer_id]
        if len(matches) != 1:
            raise TrialError('offer_unavailable')
        return matches[0]

    def instances(self, timeout):
        _, data = self.request('GET', '/instances/', timeout=timeout, params={'owner': 'me'})
        return data['instances']

    def account(self, timeout):
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            instances = pool.submit(self.instances, timeout=timeout)
            _, data = self.request('GET', '/users/current/', timeout=timeout)
            return dict(credit=data.get('credit'), instances=instances.result())

    def create(self, offer_id, *, label, image, disk_gb, timeout):
        if image != IMAGE or disk_gb != 60:
            raise TrialError('create_terms')
        body = dict(client_id='me', image=image, disk=disk_gb, label=label,
                    runtype='ssh_direc ssh_proxy', price=None, cancel_unavail=True,
                    onstart='touch /root/.no_auto_tmux', env={}, force=False)
        try:
            status, data = self.request('PUT', f'/asks/{offer_id}/', body, timeout)
            if not isinstance(data, dict):
                return dict(http_status=status, success=False, error_code='invalid_json')
            return safe_receipt(dict(http_status=status, success=data.get('success') is True,
                        new_contract=data.get('new_contract'), error_code=data.get('error')))
        except TransportError as exc:
            return dict(http_status=exc.status, success=False, error_code=str(exc))

    def instance(self, iid, timeout):
        _, data = self.request('GET', f'/instances/{iid}/', timeout=timeout, params={'owner': 'me'})
        return data['instances']

    def destroy(self, iid, timeout):
        _, data = self.request('DELETE', f'/instances/{iid}/', timeout=timeout)
        if data.get('success') is not True:
            raise TrialError('destroy_unconfirmed')

    def billing(self, iid, started_epoch, timeout):
        _, data = self.request('GET', '/users/me/invoices', timeout=timeout,
                               params=dict(owner='me', sdate=started_epoch, edate=time.time(), inc_charges=True))
        rows = [r for r in data.get('invoices', []) if r.get('instance_id') == iid]
        numbers = [r.get('amount') for r in rows]
        valid = all(type(n) in (int, float) and math.isfinite(n) for n in numbers)
        return dict(status='snapshot_not_final_invoice', charge_rows=len(rows),
                    amount_usd=sum(numbers) if valid else None, delayed_billing_possible=True)


def fingerprint(key_path):
    import paramiko
    try:
        key = paramiko.PKey.from_path(str(key_path))
        return hashlib.sha256(key.asbytes()).hexdigest()
    except Exception:
        raise TrialError('ssh_key_unreadable') from None


def code_paths(root):
    # Ровно исполняемый путь + общие gates. Изменение любого требует нового prepare.
    return [root / 'scripts/vast_hardware_trial.py', root / 'scripts/vast_hardware_probe.py',
            root / 'src/submoon_research/tracking.py', root / 'configs/compute/vast_radar.yaml',
            root / 'configs/compute/profiles/p2_2a_station.yaml', root / 'configs/compute/cpu_catalog.yaml',
            *sorted((root / 'src/submoon_research/compute').glob('*.py'))]


def prepare(root, output, key_path):
    import paramiko
    import platform
    import requests
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(root):
        raise TrialError('prepare_output_exists_or_external')
    from submoon_research.compute.radar import load_config
    cfg = load_config(root / 'configs/compute/vast_radar.yaml')
    if cfg['trial_approval_ref'] != 'W2-D043':
        raise TrialError('approval_config')
    probe = root / 'scripts/vast_hardware_probe.py'
    compile(probe.read_text(encoding='utf-8'), str(probe), 'exec')
    # Только наличие локального источника, без чтения секрета и без сети.
    auth = bool(os.environ.get('VAST_API_KEY')) or any(p.is_file() for p in
           (Path.home() / '.config/vastai/vast_api_key', Path.home() / '.vast_api_key'))
    value = dict(schema_version=1, kind='offline_preparation_not_rental_approval',
                 image=IMAGE, credential_configured=auth, ssh_key_sha256=fingerprint(key_path),
                 python=platform.python_version(), paramiko=paramiko.__version__, requests=requests.__version__,
                 cli_available=bool(shutil.which('vastai')), cli_required=False,
                 hashes={p.relative_to(root).as_posix(): digest(p) for p in code_paths(root)},
                 transport='direct_rest_single_create', paid_actions=0,
                 prepared_epoch=time.time(), api_schema_live_verified=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
    return value


def verify_prepared(root, prepared, key_path):
    value = load(prepared)
    if (value.get('schema_version') != 1 or value.get('kind') != 'offline_preparation_not_rental_approval'
            or value.get('image') != IMAGE):
        raise TrialError('prepare_schema')
    expected = {p.relative_to(root).as_posix(): digest(p) for p in code_paths(root)}
    if value.get('hashes') != expected or value.get('ssh_key_sha256') != fingerprint(key_path):
        raise TrialError('preparation_changed')
    import platform
    if value.get('python') != platform.python_version():
        raise TrialError('python_changed')
    for package in ('paramiko', 'requests'):
        if value.get(package) != importlib.metadata.version(package):
            raise TrialError('dependencies_changed')


def measurements(data):
    """Перевести видимые CPU/RAM в явные единицы, не скрывая неизвестные лимиты."""
    cgroup = data.get('cgroup', {})
    quota, quota_state = None, 'unknown'
    v2 = (cgroup.get('cpu.max') or '').split()
    if len(v2) == 2:
        if v2[0] == 'max':
            quota_state = 'unlimited_visible'
        elif int(v2[1]) > 0:
            quota, quota_state = int(v2[0]) / int(v2[1]), 'limited'
    elif cgroup.get('cpu/cpu.cfs_quota_us') is not None:
        n = int(cgroup['cpu/cpu.cfs_quota_us'])
        if n < 0:
            quota_state = 'unlimited_visible'
        elif int(cgroup.get('cpu/cpu.cfs_period_us') or 0) > 0:
            quota, quota_state = n / int(cgroup['cpu/cpu.cfs_period_us']), 'limited'
    mem_total = None
    for line in (data.get('meminfo') or '').splitlines():
        if line.startswith('MemTotal:'):
            mem_total = int(line.split()[1]) * 1024
    limit = cgroup.get('memory.max') or cgroup.get('memory/memory.limit_in_bytes')
    memory_cap = int(limit) if limit and limit != 'max' else None
    usable = min(n for n in (mem_total, memory_cap) if n is not None) if mem_total is not None else memory_cap
    model = None
    lscpu = data.get('lscpu', {})
    if lscpu.get('returncode') == 0:
        for item in json.loads(lscpu['stdout']).get('lscpu', []):
            if item.get('field', '').strip().lower() == 'model name:':
                model = item.get('data')
    return dict(cpu_model=model, affinity_vcpus=len(data.get('affinity', [])),
                physical_cores=len(data.get('physical_groups', [])) if data.get('topology_known') else None,
                quota_vcpus=quota, quota_state=quota_state,
                host_ram_bytes=mem_total, memory_limit_bytes=memory_cap,
                usable_ram_gb=usable / 1e9 if usable is not None else None)


class SSHProbe:
    def __init__(self, source, key_path, clock=time):
        self.source, self.key_path, self.clock = Path(source), Path(key_path), clock

    def run(self, info, folder, run_id, timeout):
        return bounded(_ssh, (self.source, self.key_path, info, folder, run_id, timeout), timeout)

    def _run(self, info, folder, run_id, timeout):
        import paramiko
        if not re.fullmatch(r'submoon-hw-[a-f0-9]{24}', run_id):
            raise TrialError('invalid_run_id')
        end = self.clock.monotonic() + timeout

        def remaining():
            left = end - self.clock.monotonic()
            if left <= 0:
                raise TrialError('probe_deadline')
            return left

        def command(text, limit=65536):
            _, out, err = client.exec_command(text, timeout=min(remaining(), 245))
            out.channel.settimeout(min(remaining(), 245))
            data = out.read(limit + 1)
            errors = err.read(limit + 1)
            if len(data) > limit or len(errors) > limit:
                raise TrialError('ssh_output_limit')
            if out.channel.recv_exit_status() != 0:
                raise TrialError('remote_command_failed')
            return data.decode('utf-8', 'replace')

        if self.source.stat().st_size > min(TRANSFER_BYTES, 100_000):
            raise TrialError('upload_limit')
        host, port = info.get('ssh_host'), info.get('ssh_port')
        if not isinstance(host, str) or not host or not str(port).isdigit():
            raise TrialError('ssh_endpoint_missing')
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(host, port=int(port), username='root', key_filename=str(self.key_path),
                           timeout=min(10, remaining()), auth_timeout=min(10, remaining()),
                           banner_timeout=min(10, remaining()), allow_agent=False, look_for_keys=False)
            client.save_host_keys(str(folder / 'known_hosts'))
            # onstart ставит marker; PTY fallback нужен для старых host wrappers.
            try:
                command('printf SUBMOON_SSH_OK')
            except TrialError:
                shell = client.invoke_shell()
                shell.settimeout(min(10, remaining()))
                shell.send('touch /root/.no_auto_tmux; echo SUBMOON_TMUX_DISABLED\n')
                self.clock.sleep(min(2, remaining()))
                shell.close()
                command('printf SUBMOON_SSH_OK')
            remote = '/workspace/' + run_id
            command('test ! -e ' + shlex.quote(remote) + '; mkdir ' + shlex.quote(remote))
            sftp = client.open_sftp()
            sftp.get_channel().settimeout(min(20, remaining()))
            try:
                source = self.source.read_bytes()
                with sftp.open(remote + '/probe.py', 'wx') as stream:
                    stream.write(source)
                expected = command('sha256sum ' + remote + '/probe.py').split()[0]
                if expected != hashlib.sha256(source).hexdigest():
                    raise TrialError('upload_sha')
                script_error = None
                try:
                    command(f'timeout --kill-after=5s 230s python3 {remote}/probe.py > {remote}/hardware.json '
                            f'2> {remote}/stderr.txt')
                except Exception:
                    script_error = 'remote_probe_failed'
                downloaded = 0
                data = None
                for name, cap in (('hardware.json', MAX_HARDWARE), ('stderr.txt', 16_384)):
                    remaining()
                    try:
                        with sftp.open(remote + '/' + name, 'rb') as stream:
                            content = stream.read(cap + 1)
                    except OSError:
                        continue
                    downloaded += len(content)
                    if len(content) > cap or downloaded > TRANSFER_BYTES:
                        raise TrialError('download_limit')
                    if name == 'hardware.json':
                        expected = command('sha256sum ' + remote + '/hardware.json').split()[0]
                        if expected != hashlib.sha256(content).hexdigest():
                            raise TrialError('download_sha')
                        with (folder / name).open('xb') as stream:
                            stream.write(content)
                        try:
                            data = json.loads(content)
                        except ValueError:
                            script_error = 'invalid_probe_json'
                    # stderr не копируется: shell wrapper может вывести секреты.
                save(folder / 'transfer.json', dict(upload_bytes=len(source), download_bytes=downloaded,
                     probe_sha256=hashlib.sha256(source).hexdigest(), error_code=script_error))
                if script_error or not isinstance(data, dict) or data.get('complete') is not True:
                    raise TrialError(script_error or 'incomplete_probe')
                return dict(completed=True, hardware_sha256=digest(folder / 'hardware.json'),
                            upload_bytes=len(source), download_bytes=downloaded,
                            checks=data['checks'], limitations=data['limitations'], measured=measurements(data))
            finally:
                sftp.close()
        except TrialError:
            raise
        except Exception:
            raise TrialError('ssh_or_transfer_failed') from None
        finally:
            client.close()
