"""Операторский аппаратный тест W2-D043; радар этот модуль не импортирует.

API, SSH и часы инъецируются для офлайн-приёмки. Один durable token на источник
сообщения и эпизод радара, OS-lock на все исполнения, без повторов create.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from submoon_research.compute import radar
from submoon_research.tracking import writer_lock

APPROVAL = 'W2-D043'
IMAGE = 'python:3.11-slim-bookworm'
SECONDS = 1800
RESERVE = 180
TRANSFER_BYTES = 10_000_000
PREFIX = 'submoon-hw-'
TERMINAL = {'rejected', 'create_refused', 'destroyed_verified'}


class TrialError(RuntimeError):
    """Только контролируемый машинный код; не произвольное сообщение сети."""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def positive_int(value):
    return type(value) is int and value > 0


def validate_signal(value):
    required = {'schema_version', 'approval_ref', 'user_found_station', 'source_ref',
                'user_message', 'episode_id', 'offer_id', 'machine_id', 'task_id', 'owner'}
    if not isinstance(value, dict) or set(value) != required:
        raise TrialError('signal_schema')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['approval_ref'] != APPROVAL or value['user_found_station'] is not True:
        raise TrialError('user_signal_required')
    for key in ('source_ref', 'user_message', 'episode_id', 'owner'):
        if not isinstance(value[key], str) or not 1 <= len(value[key].strip()) <= 4000:
            raise TrialError('signal_text')
    if not re.fullmatch(r'W2-T\d{3,}', str(value['task_id'])):
        raise TrialError('signal_task')
    if not all(positive_int(value[k]) for k in ('offer_id', 'machine_id')):
        raise TrialError('signal_ids')
    return value


def source_key(signal):
    return hashlib.sha256(signal['source_ref'].encode()).hexdigest()[:24]


def episode_key(signal):
    # offer_id может смениться в том же эпизоде: это не новый token.
    return hashlib.sha256(f"{signal['machine_id']}:{signal['episode_id']}".encode()).hexdigest()[:24]


def safe_receipt(receipt):
    receipt = receipt if isinstance(receipt, dict) else {}
    code = receipt.get('error_code')
    allowed = {'no_such_ask', 'insufficient_credit', 'unavailable', 'http_error',
               'transport_error', 'invalid_json', 'response_too_large', 'unknown'}
    status = receipt.get('http_status')
    iid = receipt.get('new_contract')
    return dict(http_status=status if type(status) is int and 100 <= status <= 599 else None,
                success=receipt.get('success') is True,
                new_contract=iid if positive_int(iid) else None,
                error_code=code if isinstance(code, str) and code in allowed else 'unknown')


def inventory(rows):
    if not isinstance(rows, list):
        raise TrialError('inventory_schema')
    result = []
    for r in rows:
        if not isinstance(r, dict) or not positive_int(r.get('id')):
            raise TrialError('inventory_schema')
        result.append({k: r.get(k) for k in ('id', 'machine_id', 'label', 'actual_status')})
    return result


def check_offer(offer, signal, cfg):
    if offer.get('id') != signal['offer_id'] or offer.get('machine_id') != signal['machine_id']:
        raise TrialError('offer_identity')
    row, reason = radar.assess(offer, cfg)
    if reason:
        raise TrialError('offer_' + reason)
    if (offer.get('machine_id') == 139652 or offer.get('cpu_arch') != 'amd64'
            or offer.get('resource_type') not in ('gpu', 'cpu', 'compute')
            or offer.get('verification') not in ('verified', 'unverified')
            or offer.get('rentable') is not True or offer.get('rented') is not False
            or row['vcpu'] < 64 or row['share'] < .95
            or row['ram_gb'] < 64 or row['reliability'] < .97 or row['direct_ports'] < 1
            or row['estimate']['workers'] < 28 or row['estimate']['hours'][1] > 16
            or row['estimate']['usd'][1] > 11 or row['estimate_total_usd'] > 13):
        raise TrialError('fixed_resource_or_estimate_gate')
    # assess не должен получать возможность расширить фиксированные пределы D043.
    hourly = row['usd_per_hour']
    cap = .65 if re.search(r'\b9975wx\b', offer.get('cpu_name') or '', re.I) else .75
    if not isinstance(hourly, (int, float)) or not math.isfinite(hourly) or not 0 < hourly <= cap:
        raise TrialError('hourly_cap')
    reported = offer.get('dph_total')
    if (isinstance(reported, bool) or not isinstance(reported, (int, float))
            or not math.isfinite(reported) or not math.isclose(reported, hourly, rel_tol=1e-6, abs_tol=1e-6)):
        raise TrialError('inconsistent_total_price')
    rates = []
    for name in ('inet_down_cost', 'inet_up_cost'):
        value = offer.get(name)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise TrialError('unknown_traffic_price')
        if value > .02:
            raise TrialError('traffic_price_cap')
        rates.append(value)
    # Дополнительный резерв $0.10; высокая ставка трафика не считается бесплатной.
    if hourly * .5 + sum(rates) * TRANSFER_BYTES / 1e9 + .10 > 1:
        raise TrialError('total_budget')
    return dict(hourly_usd=hourly, disk_gb=60, traffic_usd_per_gb=rates,
                advertised_vcpus=row['vcpu'], advertised_ram_gb=row['ram_gb'],
                advertised_cpu=row['cpu_name'],
                model_workers=row['estimate']['workers'], budget_usd=1, max_seconds=SECONDS)


def runtime(root, manifest, folder, active):
    path = root / 'tracking/runtime.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    with writer_lock(root):
        value = load(path) if path.exists() else dict(schema_version='1.0', processes=[])
        rows = [r for r in value['processes'] if r.get('run_id') != manifest['run_id']]
        if active:
            rows.append(dict(run_id=manifest['run_id'], task_id=manifest['signal']['task_id'],
                             owner=manifest['signal']['owner'], kind='paid_hardware_trial',
                             pid=os.getpid(), host='vast.ai', instance_id=manifest.get('instance_id'),
                             label=manifest['label'], status=manifest['status'],
                             last_checked_utc=manifest['updated_utc'], approval_ref=APPROVAL,
                             deadline_epoch=manifest['deadline_epoch'], max_budget_usd=1,
                             manifest=(folder / 'manifest.json').relative_to(root).as_posix(),
                             stop='scripts/vast_hardware_trial.py recover --run-id ' + manifest['run_id'],
                             note='Непрерывный исполнитель; независимого watchdog нет.'))
        value.update(updated_utc=manifest['updated_utc'], processes=rows)
        save(path, value)


class Runner:
    def __init__(self, root, api, probe, *, clock=time, cfg=None):
        self.root = Path(root).resolve()
        self.base = self.root / 'data/interim/vast_trials'
        self.api, self.probe, self.clock = api, probe, clock
        self.cfg = radar.load_config() if cfg is None else cfg
        self.manifest = None
        self.folder = None
        self.end_mono = None

    def remaining(self):
        return min(self.end_mono - self.clock.monotonic(),
                   self.manifest['deadline_epoch'] - self.clock.time())

    def timeout(self, cap=20):
        return max(.1, min(cap, self.remaining()))

    def record(self, status=None, **values):
        if status:
            self.manifest['status'] = status
        self.manifest.update(values, updated_utc=utc(self.clock.time()))
        save(self.folder / 'manifest.json', self.manifest)
        # События неизменяемы, финальный manifest — текущее состояние.
        n = len(list(self.folder.glob('event-*.json')))
        save(self.folder / f'event-{n:03d}.json', self.manifest)

    def _runtime(self, active):
        for _ in range(10):
            try:
                runtime(self.root, self.manifest, self.folder, active)
                return
            except ValueError:
                self.clock.sleep(.1)
        raise TrialError('runtime_registry_locked')

    def _best_record(self, status=None, **values):
        # Сломанная выгрузка/диск/реестр не должны препятствовать DELETE.
        try:
            self.record(status, **values)
        except Exception:
            self.manifest['evidence_write_failed'] = True

    def _claim(self, signal):
        self.base.mkdir(parents=True, exist_ok=True)
        (self.base / 'claims').mkdir(exist_ok=True)
        # Выполняется под общим OS-lock. Даже crash между двумя claim консервативно
        # блокирует повтор, а не разрешает второй create.
        for name in ('source-' + source_key(signal), 'episode-' + episode_key(signal)):
            with (self.base / 'claims' / (name + '.json')).open('x', encoding='utf-8') as stream:
                json.dump(dict(source_ref=signal['source_ref'], run_id=PREFIX + source_key(signal)), stream)
                stream.flush()
                os.fsync(stream.fileno())
        run_id = PREFIX + source_key(signal)
        self.folder = self.base / run_id
        self.folder.mkdir(exist_ok=False)
        self.manifest = dict(schema_version=1, run_id=run_id, label=run_id, signal=signal,
                             status='preflight', create_requests=0, instance_id=None,
                             scientific_series_started=False, independent_watchdog=False,
                             created_utc=utc(self.clock.time()))
        self.record()

    def _pending(self):
        for path in self.base.glob(PREFIX + '*/manifest.json'):
            if load(path)['status'] not in TERMINAL:
                raise TrialError('unresolved_trial_requires_recovery')

    def run(self, signal):
        entered = self.clock.monotonic()
        signal = validate_signal(signal)
        with radar.single_instance(self.base):
            self._pending()
            try:
                self._claim(signal)
            except FileExistsError:
                raise TrialError('duplicate_signal_or_episode') from None
            try:
                # Свежая точечная проверка идёт параллельно с состоянием аккаунта.
                def fresh():
                    o = self.api.offer(signal['offer_id'], signal['machine_id'], timeout=20)
                    return o, self.clock.monotonic()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    a = pool.submit(self.api.account, timeout=20)
                    b = pool.submit(fresh)
                    account, (offer, checked) = a.result(), b.result()
                rows = inventory(account['instances'])
                if any(str(r.get('label') or '').startswith(PREFIX) for r in rows):
                    raise TrialError('existing_hardware_contract')
                credit = account.get('credit')
                if isinstance(credit, bool) or not isinstance(credit, (int, float)) or not math.isfinite(credit) or credit < 1:
                    raise TrialError('insufficient_credit')
                terms = check_offer(offer, signal, self.cfg)
                if self.clock.monotonic() - checked > 30:
                    raise TrialError('stale_offer')
                # Сохранить только разрешённые публичные поля, не всё тело ответа.
                save(self.folder / 'offer.json', {k: offer.get(k) for k in radar.PUBLIC_FIELDS})
                save(self.folder / 'account_before.json', dict(instances=rows, enough_credit=True))
                self.end_mono = self.clock.monotonic() + SECONDS
                self.record('creating', create_requests=1, terms=terms,
                            runner_precreate_seconds=self.clock.monotonic() - entered,
                            started_epoch=self.clock.time(), deadline_epoch=self.clock.time() + SECONDS)
                # Unknown instance_id тоже регистрируется: crash/timeout требует recovery.
                self._runtime(True)
                try:
                    response = self.api.create(signal['offer_id'], label=self.manifest['label'],
                                               image=IMAGE, disk_gb=60, timeout=self.timeout(45))
                    receipt = safe_receipt(response)
                except Exception:
                    receipt = safe_receipt(dict(error_code='transport_error'))
                # Удержать ID в памяти ДО любой записи на диск: отказ диска не
                # должен лишить finally возможности удалить принятый контракт.
                iid = receipt['new_contract']
                if iid is not None:
                    self.manifest['instance_id'] = iid
                save(self.folder / 'create_receipt.json', receipt)
                self.record('reconciling', create_receipt=receipt)
                explicit_refusal = (receipt['http_status'] is not None and 400 <= receipt['http_status'] < 500
                                    and receipt['error_code'] in {'no_such_ask', 'unavailable', 'insufficient_credit'})
                if iid is not None:
                    self.record(instance_id=iid)
                    self._runtime(True)
                else:
                    self._reconcile(wait=not explicit_refusal)
                if self.manifest.get('instance_id') is None:
                    # Только явный структурированный отказ + отсутствие своей label
                    # допускает terminal refusal; timeout остаётся needs_attention.
                    if explicit_refusal:
                        self.record('create_refused')
                        self._runtime(False)
                        return self.manifest
                    raise TrialError('create_ambiguous')
                self._exercise()
            except BaseException as exc:
                code = str(exc) if isinstance(exc, TrialError) else type(exc).__name__
                self.record(error_code=code)
                if self.manifest['create_requests'] == 0:
                    self.record('rejected')
                else:
                    self.record('needs_attention')
            finally:
                if (self.manifest['create_requests'] and self.manifest.get('instance_id') is None
                        and self.manifest['status'] != 'create_refused'):
                    # Сюда попадаем и при отказе диска после неоднозначного create.
                    try:
                        self._reconcile()
                    except Exception:
                        self.manifest['reconciliation_failed'] = True
                if self.manifest.get('instance_id') is not None:
                    self._cleanup()
                elif self.manifest['create_requests'] and self.manifest['status'] != 'create_refused':
                    self._best_record('needs_attention')
                    try:
                        self._runtime(True)
                    except Exception:
                        self.manifest['runtime_update_failed'] = True
            return self.manifest

    def _matches(self, rows):
        matches = [r for r in inventory(rows) if r.get('label') == self.manifest['label']]
        if len(matches) > 1:
            raise TrialError('multiple_contracts_need_operator')
        if matches and matches[0].get('machine_id') != self.manifest['signal']['machine_id']:
            raise TrialError('contract_machine_mismatch')
        return matches

    def _reconcile(self, wait=True):
        while self.remaining() > RESERVE:
            try:
                matches = self._matches(self.api.instances(timeout=self.timeout()))
                if matches:
                    self._best_record(instance_id=matches[0]['id'])
                    try:
                        self._runtime(True)
                    except Exception:
                        self.manifest['runtime_update_failed'] = True
                    return
            except TrialError:
                raise
            except Exception:
                pass
            if not wait:
                return
            self.clock.sleep(min(10, max(0, self.remaining() - RESERVE)))

    def _exercise(self):
        iid = self.manifest['instance_id']
        while self.remaining() > 600:
            info = self.api.instance(iid, timeout=self.timeout())
            if info.get('id') != iid or not self._matches([info]):
                raise TrialError('contract_identity')
            state = info.get('actual_status')
            self.record('provisioning', provider_status=state if state in
                        {'created', 'loading', 'running', 'stopped', 'exited', 'offline', 'unknown'} else None)
            if state in {'stopped', 'exited', 'offline', 'unknown'}:
                raise TrialError('provisioning_failed')
            if state == 'running':
                try:
                    self.record('probing')
                    self._runtime(True)
                    outcome = self.probe.run(info, self.folder, self.manifest['run_id'],
                                             timeout=min(300, self.remaining() - RESERVE))
                    if self.remaining() < RESERVE:
                        raise TrialError('probe_deadline')
                    if not isinstance(outcome, dict) or outcome.get('completed') is not True:
                        raise TrialError('incomplete_probe')
                    self.record('probe_completed', probe=outcome)
                    checks = outcome.get('checks', {})
                    if not checks or any(value is not True for value in checks.values()):
                        raise TrialError('probe_checks_failed')
                    # Результат работы CPU не равен совпадению выделенных ресурсов.
                    measured = outcome.get('measured', {})
                    expected = self.manifest['terms']
                    quota_match = None
                    if measured.get('quota_vcpus') is not None:
                        quota_match = measured['quota_vcpus'] >= expected['advertised_vcpus']
                    elif measured.get('quota_state') == 'unlimited_visible':
                        quota_match = measured.get('affinity_vcpus') == expected['advertised_vcpus']
                    comparison = dict(
                        cpu_model_matches=None if not measured.get('cpu_model') else
                        radar.finder.cpu_label(measured['cpu_model']) == radar.finder.cpu_label(expected['advertised_cpu']),
                        cpu_quota_matches=quota_match,
                        affinity_matches=None if measured.get('affinity_vcpus') is None else
                        measured['affinity_vcpus'] == expected['advertised_vcpus'],
                        memory_meets_advertised=None if measured.get('usable_ram_gb') is None else
                        measured['usable_ram_gb'] >= expected['advertised_ram_gb'] * .99)
                    self.record(resource_comparison=comparison, memory_comparison_tolerance_fraction=.01)
                    if not all(v is True for v in comparison.values()):
                        raise TrialError('advertised_resources_mismatch_or_unknown')
                    return
                except TrialError:
                    raise
                except Exception:
                    # Probe не повторяется автоматически: он мог уже завершить нагрузку.
                    raise TrialError('ssh_or_probe_failed') from None
            self.clock.sleep(10)
        raise TrialError('provisioning_deadline')

    def _cleanup(self):
        iid = self.manifest['instance_id']
        deleted = False
        delete_acknowledged = False
        for _ in range(3):
            try:
                rows = inventory(self.api.instances(timeout=max(10, self.timeout())))
                found = [r for r in rows if r['id'] == iid]
                if not found and not delete_acknowledged:
                    # Inventory может запаздывать сразу после create. Точечный GET
                    # позволяет всё равно удалить реально существующий контракт.
                    try:
                        info = self.api.instance(iid, timeout=max(10, self.timeout()))
                        found = inventory([info])
                    except Exception as exc:
                        if getattr(exc, 'status', None) not in (404, 410):
                            raise
                        delete_acknowledged = True  # провайдер явно подтвердил отсутствие
                if not found:
                    self.clock.sleep(2)
                    rows = inventory(self.api.instances(timeout=max(10, self.timeout())))
                    if not any(r['id'] == iid for r in rows) and delete_acknowledged:
                        deleted = True
                        break
                    continue
                if not self._matches(found):
                    raise TrialError('destroy_identity_mismatch')
                self._best_record('destroying')
                # DELETE идемпотентен; повтор не создаёт контрактов.
                self.api.destroy(iid, timeout=max(10, self.timeout()))
                delete_acknowledged = True
                self.clock.sleep(min(2, max(0, self.remaining())))
            except Exception as exc:
                code = str(exc) if isinstance(exc, TrialError) else type(exc).__name__
                self._best_record(cleanup_error=code)
                if isinstance(exc, TrialError):
                    break
        if not deleted:
            try:
                first = inventory(self.api.instances(timeout=max(10, self.timeout())))
                self.clock.sleep(2)
                second = inventory(self.api.instances(timeout=max(10, self.timeout())))
                deleted = delete_acknowledged and not any(r['id'] == iid for r in first + second)
            except Exception:
                pass
        self._best_record('destroyed_verified' if deleted else 'needs_attention',
                    elapsed_seconds=max(0, self.clock.time() - self.manifest['started_epoch']),
                    compute_disk_cost_estimate_usd=max(0, self.clock.time() - self.manifest['started_epoch'])
                    / 3600 * self.manifest['terms']['hourly_usd'], cost_is_estimate_not_invoice=True)
        # Сначала освободить ресурс, затем необязательная проверка биллинга.
        try:
            self._runtime(not deleted)
        except Exception:
            self.manifest['runtime_update_failed'] = True
        if deleted:
            try:
                bill = self.api.billing(iid, self.manifest['started_epoch'], timeout=10)
                save(self.folder / 'billing.json', bill)
            except Exception:
                try:
                    save(self.folder / 'billing.json', dict(status='unverified', delayed_billing_possible=True))
                except Exception:
                    self.manifest['evidence_write_failed'] = True
        try:
            save(self.folder / 'sha256.json', {p.name: digest(p) for p in self.folder.iterdir()
                                             if p.is_file() and p.name != 'sha256.json'})
        except Exception:
            self.manifest['evidence_write_failed'] = True

    def recover(self, run_id):
        """Только reconciliation/удаление. Никогда не вызывает create или probe."""
        if not re.fullmatch(PREFIX + r'[a-f0-9]{24}', run_id):
            raise TrialError('invalid_run_id')
        with radar.single_instance(self.base):
            self.folder = self.base / run_id
            self.manifest = load(self.folder / 'manifest.json')
            if self.manifest['run_id'] != run_id or self.manifest['label'] != run_id:
                raise TrialError('manifest_identity')
            if self.manifest['status'] in TERMINAL:
                self._runtime(False)
                return self.manifest
            if self.manifest.get('create_requests', 0) == 0:
                self.record('rejected', error_code='interrupted_before_create')
                return self.manifest
            self.end_mono = self.clock.monotonic() + max(0, self.manifest['deadline_epoch'] - self.clock.time())
            if self.manifest.get('instance_id') is None:
                # Даже после дедлайна сделать ограниченную сверку; нельзя оставить
                # поздно появившийся инстанс только из-за истечения прежнего окна.
                matches = self._matches(self.api.instances(timeout=20))
                if matches:
                    self.record(instance_id=matches[0]['id'])
            if self.manifest.get('instance_id') is not None:
                self._cleanup()
            else:
                self.record('needs_attention', error_code='create_ambiguous')
                self._runtime(True)
            return self.manifest
