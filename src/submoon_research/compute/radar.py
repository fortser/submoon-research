"""Постоянный read-only радар Vast.ai: finder, SQLite, эпизоды уведомлений.

Не импортирует инструменты аренды и не читает approval/ключи аккаунта.
Отсутствие в неполной выдаче не означает исчезновения; повторный сигнал взводится
только после явного отрицательного ответа о той же аллокации.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any

import yaml

from submoon_research.compute import finder
from submoon_research.compute.market_search import (
    MarketConfig, PUBLIC_FIELDS, SearchError, default_request,
)

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT/'configs/compute/vast_radar.yaml'
OUTPUT = ROOT/'data/interim/vast_radar'
FINDINGS = ROOT/'docs/compute/interesting_stations.md'


class AlreadyRunning(RuntimeError):
    pass


def utc(epoch=None):
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat(timespec='seconds')


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)


def load_config(path=CONFIG):
    cfg = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    expected = set(yaml.safe_load(CONFIG.read_text(encoding='utf-8')))
    if set(cfg) != expected or cfg['schema_version'] != 1:
        raise ValueError('Неверная схема/неизвестные поля radar config')
    for key in ('interval_seconds', 'review_after_days', 'search_seconds', 'max_requests_per_cycle',
                'max_recheck_machines', 'request_spacing_seconds', 'request_timeout_seconds',
                'retention_days', 'max_output_mib', 'port', 'target_max_hourly_usd',
                'max_hourly_usd', 'min_workers', 'max_estimated_hours', 'max_estimated_compute_usd',
                'full_run_budget_usd', 'transfer_reserve_usd', 'wrapup_hours', 'trial_hours',
                'trial_budget_usd'):
        value = cfg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError('Неверный положительный предел: '+key)
    if cfg['interval_seconds'] < 900 or not 1024 <= cfg['port'] <= 65535:
        raise ValueError('Опрос не чаще 15 мин; порт 1024..65535')
    if (cfg['automatic_rental'] is not False or cfg['max_paid_attempts'] != 1 or cfg['paid_retries'] != 0
            or cfg['max_hourly_usd'] > .75 or cfg['trial_hours'] > .5 or cfg['trial_budget_usd'] > 1
            or cfg['full_run_budget_usd'] > 13 or cfg['target_max_hourly_usd'] > cfg['max_hourly_usd']):
        raise ValueError('Нельзя расширять разрешённые расходы через конфигурацию')
    for key in ('max_requests_per_cycle', 'max_recheck_machines', 'retention_days', 'port'):
        if int(cfg[key]) != cfg[key]:
            raise ValueError('Требуется целое: '+key)
    if cfg['max_requests_per_cycle'] > 90 or cfg['max_recheck_machines'] > 10:
        raise ValueError('Слишком большой бюджет запросов')
    re.compile(cfg['target_cpu_regex'])
    for item in cfg['watch']:
        if not isinstance(item['machine_id'], int) or not isinstance(item['offer_id'], int):
            raise ValueError('watch требует числовые machine_id/offer_id')
    return cfg


def profile_for(cfg):
    return finder.load_profile(cfg['profile'], overrides=dict(max_hourly_usd=cfg['max_hourly_usd']))


def allocation_key(offer):
    # offer_id/цена/verification не меняют идентичность выделенной конфигурации.
    identity = [offer.get(k) for k in ('machine_id', 'cpu_name', 'cpu_cores_effective',
                                     'cpu_ram', 'num_gpus', 'gpu_name', 'resource_type')]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]


def assess(offer, cfg, profile=None, catalog=None):
    """Жёсткий фильтр плюс простая оценка. Ни один неизвестный параметр не улучшает оценку."""
    profile, catalog = profile or profile_for(cfg), catalog or finder.Catalog.load()
    public = {k: offer.get(k) for k in PUBLIC_FIELDS}
    row, reason = finder.evaluate(public, profile, catalog)
    row['kind'] = 'target' if re.search(cfg['target_cpu_regex'], row['cpu_name'] or '') else 'analog'
    row['allocation_key'] = allocation_key(public)
    row['estimate_total_usd'] = None
    row['price_rearm'] = False
    if public.get('machine_id') in cfg['excluded_machine_ids']:
        return row, 'excluded_machine'
    if reason:
        if reason == 'price' and row['usd_per_hour'] is not None:
            row['price_rearm'] = row['usd_per_hour'] > cfg['max_hourly_usd']*1.05
        return row, reason
    # Finder умеет fallback dph_total; сигнал требует известных компонентов цены с диском.
    for key in ('dph_base', 'storage_cost'):
        value = public.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            return row, 'unknown_storage_price'
    if row['kind'] == 'target' and row['usd_per_hour'] > cfg['target_max_hourly_usd']:
        row['price_rearm'] = row['usd_per_hour'] > cfg['target_max_hourly_usd']*1.05
        return row, 'target_price'
    estimate = row.get('estimate')
    if not estimate:
        return row, 'unknown_speed'
    if estimate['workers'] < cfg['min_workers']:
        return row, 'workers'
    if estimate['hours'][1] > cfg['max_estimated_hours']:
        return row, 'series_time'
    if estimate['usd'][1] > cfg['max_estimated_compute_usd']:
        row['price_rearm'] = estimate['usd'][1] > cfg['max_estimated_compute_usd']*1.05
        return row, 'series_price'
    # Консервативно добавляем trial поверх setup и резерв выгрузки, не двойной оптимизм.
    row['estimate_total_usd'] = (estimate['usd'][1] +
        row['usd_per_hour']*(cfg['trial_hours']+cfg['wrapup_hours']) + cfg['transfer_reserve_usd'])
    if row['estimate_total_usd'] > cfg['full_run_budget_usd']:
        row['price_rearm'] = row['estimate_total_usd'] > cfg['full_run_budget_usd']*1.05
        return row, 'total_price'
    return row, None


class Store:
    def __init__(self, folder, now=None):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.folder/'radar.sqlite3', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS allocations(key TEXT PRIMARY KEY, machine INTEGER NOT NULL,
 payload TEXT NOT NULL, state TEXT NOT NULL, armed INTEGER NOT NULL, checked REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, stamp REAL NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY, stamp REAL NOT NULL, payload TEXT NOT NULL);
''')
        if self.get('started_epoch') is None:
            self.put('started_epoch', time.time() if now is None else now)

    def close(self):
        with self.lock:
            self.db.close()

    def get(self, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def put(self, key, value):
        with self.lock, self.db:
            self.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, json.dumps(value)))

    def rows(self):
        with self.lock:
            return [dict(json.loads(p), state=s, armed=bool(a), checked_epoch=t, checked_utc=utc(t))
                    for p, s, a, t in self.db.execute('SELECT payload,state,armed,checked FROM allocations')]

    def recent_events(self):
        with self.lock:
            return [dict(json.loads(p), event_id=i, utc=utc(t)) for i, t, p in self.db.execute(
                'SELECT id,stamp,payload FROM events ORDER BY id DESC LIMIT 30')]

    def observe(self, offer, row, reason, now):
        """Один сигнал на эпизод; SQL commit до звука: после сбоя возможен пропуск, не дубликат."""
        key = row['allocation_key']
        qualifies = reason is None
        explicit_no = (offer.get('rentable') is False or offer.get('rented') is True)
        state = 'available' if qualifies else 'unavailable' if explicit_no else 'excluded'
        payload = dict(row, reason=reason, offer=offer)
        with self.lock, self.db:
            prior = self.db.execute('SELECT armed FROM allocations WHERE key=?', (key,)).fetchone()
            armed = bool(prior[0]) if prior else True
            event = qualifies and armed
            # Подтверждённый отрицательный ответ, не пропуск в выдаче, взводит новый эпизод.
            if not qualifies and (explicit_no or row.get('price_rearm', False)):
                armed = True
            elif qualifies:
                armed = False
            self.db.execute('INSERT OR REPLACE INTO allocations VALUES(?,?,?,?,?,?)',
                            (key, offer['machine_id'], json.dumps(payload), state, int(armed), now))
            if event:
                self.db.execute('INSERT INTO events(stamp,payload) VALUES(?,?)',
                                (now, json.dumps(payload)))
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('ever_found','true')")
                self.db.execute("INSERT OR IGNORE INTO meta VALUES('first_found_epoch',?)", (json.dumps(now),))
            return event

    def unknown(self, machine_id):
        # Не менять armed и last confirmed UTC при ошибке/неполной выдаче.
        with self.lock, self.db:
            self.db.execute("UPDATE allocations SET state='unknown' WHERE machine=? AND state='available'",
                            (machine_id,))

    def cycle(self, summary, now):
        with self.lock, self.db:
            cur = self.db.execute('INSERT INTO cycles(stamp,payload) VALUES(?,?)', (now, json.dumps(summary)))
            return cur.lastrowid

    def maintain(self, days, now):
        cutoff = now-days*86400
        with self.lock, self.db:
            self.db.execute('DELETE FROM cycles WHERE stamp<?', (cutoff,))
            self.db.execute('DELETE FROM events WHERE stamp<?', (cutoff,))
            # Не удаляем armed-состояние найденных станций: рестарт не должен повторять звук.
        for path in (self.folder/'snapshots').glob('*.json.gz'):
            if path.stat().st_mtime < cutoff:
                path.unlink()


class Radar:
    def __init__(self, cfg, store, findings=FINDINGS, *, request=None, notify=None,
                 clock=time.monotonic, wall=time.time, sleep=time.sleep):
        self.cfg, self.store, self.findings = cfg, store, Path(findings)
        self.profile, self.catalog = profile_for(cfg), finder.Catalog.load()
        identity = dict(config=cfg, profile=self.profile, catalog=finder.CATALOG.read_text(encoding='utf-8'),
                        code_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                     (Path(__file__), Path(finder.__file__),
                                      Path(__file__).with_name('market_search.py'))})
        self.identity = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        config_snapshot = store.folder/f'config-{self.identity[:16]}.json'
        if not config_snapshot.exists():
            save_json(config_snapshot, dict(identity, identity_sha256=self.identity))
        self.store.put('identity_sha256', self.identity)
        self.publish_lock = threading.RLock()
        self.transport = request or default_request
        self.notify = notify or (lambda: None)
        self.clock, self.wall, self.sleep = clock, wall, sleep
        self.last_request = None
        self.status_lock = threading.RLock()
        self.status: dict[str, Any] = dict(running=True, polling=False, last_cycle_utc=None, last_success_epoch=None,
                           next_poll_epoch=wall(), errors=[], search=None, target_checks=[])
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.last_poll = None
        self.failures = 0
        self.count = 0
        self.blocked = False
        self.snapshot_offers = {}
        self.alerted_this_cycle = False

    def request(self, query, config):
        if self.blocked or self.count >= self.cfg['max_requests_per_cycle'] or self.stop.is_set():
            raise SearchError('rate_limited')  # завершает sweep; новых HTTP-запросов нет
        if self.last_request is not None:
            self.sleep(max(0., self.cfg['request_spacing_seconds']-(self.clock()-self.last_request)))
        self.last_request = self.clock()
        self.count += 1
        try:
            offers = self.transport(query, config)
        except SearchError as exc:
            if exc.kind in ('rate_limited', 'authentication_or_access_denied', 'daily_quota'):
                self.blocked = True
            raise
        # Не доверяем mock/транспорту с лишними полями: сохраняются только публичные.
        offers = [{k: (None if isinstance(offer.get(k), float) and not math.isfinite(offer[k])
                        else offer.get(k)) for k in PUBLIC_FIELDS} for offer in offers]
        for offer in offers:
            self.snapshot_offers[offer['id']] = offer
        return offers

    def probe(self, machine_id):
        config = MarketConfig(storage_gb=self.profile['requirements']['disk_gb'],
                              timeout=self.cfg['request_timeout_seconds'])
        try:
            offers = self.request(f'machine_id={int(machine_id)}', config)
        except SearchError as exc:
            self.store.unknown(machine_id)
            return dict(machine_id=machine_id, status='error', reason=exc.kind, offers=[])
        offers = [o for o in offers if o.get('machine_id') == machine_id and isinstance(o.get('id'), int)]
        if not offers:
            self.store.unknown(machine_id)
            return dict(machine_id=machine_id, status='not_observed', offers=[])
        # Объединяем одинаковые аллокации: свободный вариант важнее занятого с иным offer_id.
        grouped = {}
        for offer in offers:
            row, reason = assess(offer, self.cfg, self.profile, self.catalog)
            row['evaluation_sha256'] = self.identity
            key = row['allocation_key']
            priority = (reason is not None, offer.get('rentable') is not True,
                        row.get('usd_per_hour') if row.get('usd_per_hour') is not None else math.inf)
            if key not in grouped or priority < grouped[key][0]:
                grouped[key] = (priority, offer, row, reason)
        new = False
        for _, offer, row, reason in grouped.values():
            new = self.store.observe(offer, row, reason, self.wall()) or new
        # Старые аллокации, которых в ответе нет, не остаются зелёными и не перевзводятся.
        seen = set(grouped)
        with self.store.lock, self.store.db:
            for old in self.store.rows():
                if old['machine_id'] == machine_id and old['allocation_key'] not in seen:
                    self.store.db.execute("UPDATE allocations SET state='unknown' WHERE key=?",
                                          (old['allocation_key'],))
        self.publish()
        if (new and not self.alerted_this_cycle
                and self.store.get('sound_enabled', self.cfg['sound_enabled'])
                and self.wall() >= self.store.get('snooze_until', 0)):
            self.alerted_this_cycle = True
            try:
                self.notify()
            except Exception:
                # Находка уже записана, ошибка устройства звука не ломает сборщик.
                self.store.put('notification_error', 'sound_unavailable')
        return dict(machine_id=machine_id, status='checked', offers=[o['id'] for o in offers])

    def poll(self):
        self.count, self.blocked, self.snapshot_offers = 0, False, {}
        self.alerted_this_cycle = False
        self.last_poll = self.clock()
        if sum(p.stat().st_size for p in self.store.folder.rglob('*') if p.is_file()) > self.cfg['max_output_mib']*2**20:
            self.store.put('paused', True)
            raise RuntimeError('Лимит выходов радара достигнут')
        with self.status_lock:
            self.status.update(polling=True, errors=[], target_checks=[])
        target_checks, checked = [], set()
        try:
            for watch in self.cfg['watch']:
                target_checks.append(self.probe(watch['machine_id']))
                checked.add(watch['machine_id'])
                if self.blocked:
                    break
            # Одинаковый finder для ручного поиска и радара, без копии старого price-only сборщика.
            result = finder.find(self.profile, catalog=self.catalog, request=self.request,
                                 max_seconds=self.cfg['search_seconds']) if not self.blocked else None
            if result is not None:
                candidates = []
                for offer in result['raw'].values():
                    row, reason = assess(offer, self.cfg, self.profile, self.catalog)
                    if reason is None:
                        candidates.append((row, offer))
                candidates.sort(key=lambda item: (item[0]['kind'] != 'target',
                                item[0]['verification'] != 'verified', item[0]['estimate_total_usd']))
                machines = list(dict.fromkeys(o['machine_id'] for _, o in candidates))
                # Подтверждаем исчезновения прежних находок отдельно от неполного поиска.
                machines += [r['machine_id'] for r in self.store.rows()
                             if r['state'] in ('available', 'unknown') and r['machine_id'] not in machines]
                for mid in [m for m in machines if m not in checked][:self.cfg['max_recheck_machines']]:
                    target_checks.append(self.probe(mid))
                    checked.add(mid)
                    if self.blocked:
                        break
            errors = [c.get('reason') for c in target_checks if c['status'] == 'error']
            search = {k: v for k, v in result['search'].items() if k != 'receipts'} if result else None
            if result:
                errors += result['search']['errors']
            # Неполнота не равна сетевому отказу, но всегда остаётся видимой.
            network_errors = [e for e in errors if e not in ('request_or_time_budget',)]
            self.failures = self.failures+1 if network_errors else 0
            wait = min(7200., self.cfg['interval_seconds']*2**min(self.failures, 3))
            now = self.wall()
            with self.status_lock:
                self.status.update(last_cycle_utc=utc(now), next_poll_epoch=now+wait,
                                   errors=errors, search=search, target_checks=target_checks)
                if (any(c['status'] == 'checked' for c in target_checks)
                        or (result and any(q['status'] == 'ok' for q in result['search'].get('receipts', [])))):
                    self.status['last_success_epoch'] = now
            cycle = self.store.cycle(dict(utc=utc(now), evaluation_sha256=self.identity,
                                          requests=self.count, search=search,
                                          checks=target_checks, errors=errors), now)
            folder = self.store.folder/'snapshots'
            folder.mkdir(exist_ok=True)
            with gzip.open(folder/f'{cycle:08d}.json.gz', 'wt', encoding='utf-8') as stream:
                json.dump(dict(utc=utc(now), evaluation_sha256=self.identity,
                               offers=list(self.snapshot_offers.values())), stream,
                          ensure_ascii=False, allow_nan=False)
            self.store.maintain(self.cfg['retention_days'], now)
        finally:
            with self.status_lock:
                self.status['polling'] = False
            self.publish()
        return self.view()

    def view(self) -> dict[str, Any]:
        now = self.wall()
        with self.status_lock:
            status = dict(self.status)
        rows = self.store.rows()
        for row in rows:
            row['stale'] = now-row['checked_epoch'] > self.cfg['interval_seconds']*2
        rows.sort(key=lambda r: (r['state'] != 'available', r['kind'] != 'target',
                                r.get('estimate_total_usd') or math.inf))
        due = self.store.get('started_epoch')+self.cfg['review_after_days']*86400
        first_found = self.store.get('first_found_epoch')
        review = now >= due and (first_found is None or first_found > due)
        return dict(schema_version=1, utc=utc(now), status=status, rows=rows,
                    events=self.store.recent_events(), review_required=review,
                    review_due_utc=utc(due), evaluation_sha256=self.identity,
                    paused=self.store.get('paused', False),
                    sound_enabled=self.store.get('sound_enabled', self.cfg['sound_enabled']),
                    snooze_until=self.store.get('snooze_until', 0),
                    notification_error=self.store.get('notification_error'),
                    config=self.cfg, paid_actions=0, automatic_rental=False,
                    findings_path='docs/compute/interesting_stations.md')

    def publish(self):
        with self.publish_lock:
            self._publish()

    def _publish(self):
        data = self.view()
        save_json(self.store.folder/'latest.json', data)
        self.findings.parent.mkdir(parents=True, exist_ok=True)
        lines = ['# Интересные рабочие станции — радар Vast.ai', '',
                 'Автоматический оперативный файл. Не разрешение аренды и не резервирование.',
                 f"Обновлено: **{data['utc']}**. Монитор: "
                 f"{'пауза' if data['paused'] else 'работает' if data['status']['running'] else 'остановлен'}.",
                 f"Последний цикл: {data['status']['last_cycle_utc'] or 'ещё не выполнен'}.",
                 'Не найдено в выдаче ≠ занято. Проверять свежесть и availability перед арендой.', '',
                 '[Руководство и действия оператора](vast_radar_guide.md) · '
                 '[Разрешение проверочной аренды](trial_rental_approval.md)', '',
                 'Данные/история: `data/interim/vast_radar/latest.json`, `radar.sqlite3`, `snapshots/`.',
                 'Согласие W2-D031: одна попытка ≤$0.75/ч, ≤30 мин от create, ≤$1 всего; '
                 'без продолжения — destroy. Основной прогон не разрешён автоматически.', '']
        if data['review_required']:
            lines += ['**Пять дней без подходящих находок: нужен пересмотр бюджета с пользователем. '
                      'Лимиты автоматически НЕ повышены.**', '']
        for row in data['rows']:
            name = re.sub(r'[\r\n`<>|]', ' ', row['cpu_name'] or '?')
            lines += [f"## {name} — offer `{row['offer_id']}`, machine `{row['machine_id']}`",
                      f"- Категория: {row['kind']}; статус: **{row['state']}**; "
                      f"{'УСТАРЕЛО; ' if row['stale'] else ''}проверено {row['checked_utc']}.",
                      f"- Доступно vCPU: {row['vcpu']} / объявлено {row['host_vcpu']}; RAM {row['ram_gb']} ГБ; "
                      f"verification {row['verification']}; reliability {row['reliability']}.",
                      f"- Цена с 60 ГБ: ${row['usd_per_hour']}/ч; причина отклонения: {row.get('reason')}."]
            est = row.get('estimate')
            if est:
                lines += [f"- Модель P2-2a: {est['hours'][0]:.2f}–{est['hours'][1]:.2f} ч; "
                          f"${est['usd'][0]:.2f}–{est['usd'][1]:.2f} вычисления+диск; "
                          f"консервативный итог с резервами: {row.get('estimate_total_usd')}; "
                          f"скорость: {row['speed_confidence']} (не проверка станции)."]
            lines.append('')
        if not data['rows']:
            lines += ['Пока предложений для передачи оператору нет.', '']
        lines += ['## Последние сигналы (не обязательно ещё доступны)', '']
        for event in data['events'][:10]:
            lines.append(f"- {event['utc']}: offer `{event['offer_id']}`, machine `{event['machine_id']}`.")
        tmp = self.findings.with_suffix('.tmp')
        tmp.write_text('\n'.join(lines)+'\n', encoding='utf-8')
        tmp.replace(self.findings)

    def run(self):
        while not self.stop.is_set():
            if not self.store.get('paused', False):
                try:
                    self.poll()
                except Exception as exc:
                    # Только тип, не произвольный exception с потенциальными секретами/URL.
                    with self.status_lock:
                        self.status.update(errors=[type(exc).__name__], next_poll_epoch=self.wall()+900)
                    self.publish()
            while not self.stop.wait(1):
                due = self.wall() >= self.status['next_poll_epoch']
                manual = self.wake.is_set()
                # Кнопка не может превратиться в HTTP-шторм.
                if (due or manual) and not self.store.get('paused', False):
                    if self.last_poll is None or self.clock()-self.last_poll >= 60:
                        self.wake.clear()
                        break
        with self.status_lock:
            self.status['running'] = False
        self.publish()


@contextmanager
def single_instance(folder):
    """OS-lock освобождается при аварии/выключении; stale PID не блокирует автозапуск."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'radar.lock').open('a+b') as stream:
        # Windows запрещает даже чтение заблокированного байта второй копией.
        if stream.seek(0, 2) == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                platform_lock: Any = __import__('fcntl')
                platform_lock.flock(stream, platform_lock.LOCK_EX | platform_lock.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning('Радар уже запущен') from exc
        try:
            yield
        finally:
            stream.seek(0)
            if __import__('os').name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                platform_lock: Any = __import__('fcntl')
                platform_lock.flock(stream, platform_lock.LOCK_UN)
