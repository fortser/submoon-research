"""Монитор рынка на 48–72 h: только чтение, heartbeat, сроки, backoff и статистика."""
import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
import uuid

from submoon_research.compute.cpu_targets import parse_targets
from submoon_research.compute.market_search import MarketConfig, collect, rejection, candidate
from submoon_research.compute.market_store import MarketStore


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    temporary.replace(path)


def runtime_entry(root, folder, active, manifest):
    from submoon_research.tracking import writer_lock
    (root/'tracking').mkdir(exist_ok=True)
    path = root/'tracking/runtime.json'
    with writer_lock(root):
        existing = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else dict(schema_version='1.0',processes=[])
        entries = [p for p in existing['processes'] if p.get('market_session_id') != folder.name]
        if active:
            entries.append(dict(market_session_id=folder.name, run_id='vast-market-'+folder.name,
                kind='read_only_market_monitor', task_id='W2-T004', pid=os.getpid(),
                folder=folder.relative_to(root).as_posix(), deadline_epoch=manifest['deadline_epoch'],
                heartbeat=(folder/'heartbeat.json').relative_to(root).as_posix(),
                stop_file=(folder/'stop.requested').relative_to(root).as_posix(), paid_actions=0))
        existing.update(updated_utc=utc_now(), processes=entries)
        save(path, existing)


def coverage(text):
    targets = parse_targets(text)
    return dict(source_sha256=hashlib.sha256(text.encode()).hexdigest(), target_count=len(targets),
        exact_models=sum(not t.family for t in targets), families=sum(t.family for t in targets),
        targets=[t.as_dict() for t in targets])


def monitor(root, cfg, *, interval=300., duration_hours=72., cycles=None, folder=None, resume=False):
    cfg.validate()
    if not math.isfinite(interval) or interval < 15 or not math.isfinite(duration_hours) or not 0 < duration_hours <= 72:
        raise ValueError('Интервал >=15 s, длительность в (0,72] h')
    source = (root/'good_cpu.txt').read_text(encoding='utf-8')
    targets = parse_targets(source)
    folder = Path(folder) if folder else root/'data/interim/vast_market'/(
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8])
    folder = folder.resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError('Выходы должны находиться внутри проекта')
    manifest_path = folder/'manifest.json'
    if manifest_path.exists() and not resume:
        raise ValueError('Для существующей сессии нужен --resume')
    identity = dict(config=asdict(cfg), source_sha256=hashlib.sha256(source.encode()).hexdigest(), interval_seconds=interval)
    code_hashes = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in Path(__file__).resolve().parent.glob('*.py')}
    if resume:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest['status'] == 'completed' or manifest['identity'] != identity:
            raise ValueError('Нельзя менять область/источник или продолжать завершённую сессию')
        if manifest['code_sha256'] != code_hashes:
            raise ValueError('Версия кода изменилась; создать новую сессию, не смешивать статистику')
        if time.time() >= manifest['deadline_epoch']:
            raise ValueError('Истёк исходный срок сессии')
        manifest['resumed_utc'] = utc_now()
        manifest['status'] = 'running'
        (folder/'stop.requested').unlink(missing_ok=True)
    else:
        folder.mkdir(parents=True, exist_ok=False)
        manifest = dict(schema_version='2.0', status='running', session_id=folder.name,
            started_utc=utc_now(), deadline_epoch=time.time()+duration_hours*3600,
            identity=identity, code_sha256=code_hashes, pid=os.getpid(),
            mode='read_only_market_monitor', paid_actions=0, max_output_mib=2048,
            metadata_validation='provider_advertisement_only')
        save(manifest_path, manifest)
        (folder/'good_cpu.snapshot.txt').write_text(source, encoding='utf-8')
        save(folder/'coverage.json', coverage(source))
        (folder/'code').mkdir()
        for code_path in Path(__file__).resolve().parent.glob('*.py'):
            (folder/'code'/code_path.name).write_bytes(code_path.read_bytes())
    lock = folder/'collector.lock'
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(lock_fd, str(os.getpid()).encode())
    os.close(lock_fd)
    store = MarketStore(folder)
    store.set_targets(targets)
    (folder/'launch.pid').write_text(str(os.getpid()), encoding='utf-8')
    runtime_entry(root, folder, True, manifest)
    done, failures = 0, 0
    stop_requested = False
    try:
        while time.time() < manifest['deadline_epoch'] and (cycles is None or done < cycles):
            if (folder/'stop.requested').exists():
                stop_requested = True
                break
            started = time.monotonic()
            bounded = replace(cfg, cycle_budget=min(cfg.cycle_budget, manifest['deadline_epoch']-time.time()))
            data = collect(bounded)
            rejects = Counter()
            selected = []
            for o in data['offers']:
                reason = rejection(o, targets, cfg)
                if reason:
                    rejects[reason] += 1
                else:
                    selected.append(candidate(o, targets, cfg))
            cycle, events = store.append(utc_now(), data, selected, rejects)
            statuses = [r['status'] for r in data['queries']]
            failures = failures+1 if any(s != 'ok' for s in statuses) else 0
            wait = max(interval, min(1800., 60.*2**min(failures, 5))) if failures else interval
            save(folder/'heartbeat.json', dict(utc=utc_now(), pid=os.getpid(), cycle_id=cycle,
                complete=data['complete'], candidates=len(selected), queries=len(data['queries']),
                last_request_statuses=statuses, next_cycle_delay_seconds=wait,
                consecutive_error_cycles=failures, deadline_epoch=manifest['deadline_epoch']))
            stats = store.statistics()
            save(folder/'statistics.json', stats)
            save(folder/'latest.json', dict(utc=utc_now(), complete=data['complete'], candidates=selected, rejected=dict(rejects)))
            write_table(folder/'latest.txt', selected, data['complete'])
            manifest.update(last_utc=utc_now(), latest_cycle=cycle, complete_polls=stats['complete_polls'])
            save(manifest_path, manifest)
            print(json.dumps(dict(session=folder.relative_to(root).as_posix(), cycle=cycle,
                complete=data['complete'], found=len(selected), events=len(events),
                requests=len(data['queries']), errors=[s for s in statuses if s != 'ok']), ensure_ascii=False), flush=True)
            done += 1
            if sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()) > 2048*2**20:
                raise RuntimeError('Лимит выходов 2 GiB достигнут')
            if time.time() >= manifest['deadline_epoch'] or (cycles is not None and done >= cycles):
                break
            sleep_for = min(wait+random.uniform(0, min(5., wait*.02))-(time.monotonic()-started),
                            manifest['deadline_epoch']-time.time())
            # Серверный backoff может быть долгим, но heartbeat остаётся проверяемым.
            until = time.monotonic()+max(0, sleep_for)
            while time.monotonic() < until:
                if (folder/'stop.requested').exists():
                    stop_requested = True
                    break
                time.sleep(min(30., until-time.monotonic()))
        manifest['status'] = 'stopped' if stop_requested else 'completed'
    except KeyboardInterrupt:
        manifest['status'] = 'interrupted'
    except Exception as exc:
        manifest.update(status='failed', error_type=type(exc).__name__)
        raise
    finally:
        save(folder/'statistics.json', store.statistics())
        store.close()
        lock.unlink(missing_ok=True)
        manifest.update(finished_utc=utc_now(), paid_actions=0)
        save(manifest_path, manifest)
        save(folder/'checksums.json', {p.relative_to(folder).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file() and p.name != 'checksums.json'})
        runtime_entry(root, folder, False, manifest)
    return folder


def write_table(path, rows, complete):
    lines = [f'Вычислительные предложения; полный снимок={complete}; конфигурация заявлена провайдером.',
             'ID | CPU | $/h total | effective / host logical CPU | RAM GB | GPU | location']
    for r in sorted(rows, key=lambda r:(r['cpu_key'], r['hourly_total_usd'])):
        lines.append(f"{r['offer_id']} | {r['cpu_name_reported']} | {r['hourly_total_usd']:.8f} | "
                     f"{r['effective_cpus_reported']} / {r['host_logical_cpus_reported']} | "
                     f"{r['ram_gb_reported']} | {r['num_gpus']} {r['gpu_name']} | {r['location']}")
    path.write_text('\n'.join(lines)+'\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-price', type=float, default=1.)
    parser.add_argument('--min-cores', type=float, default=1.)
    parser.add_argument('--min-disk', type=float, default=20.)
    parser.add_argument('--storage-gb', type=float, default=20.)
    parser.add_argument('--cpu-only', action='store_true')
    parser.add_argument('--whole-machine-only', action='store_true')
    parser.add_argument('--with-gpu', action='store_true', help='Совместимость: compute с GPU уже включены по умолчанию')
    parser.add_argument('--limit', type=int, default=1000)
    parser.add_argument('--max-queries', type=int, default=64)
    parser.add_argument('--request-spacing', type=float, default=1.)
    parser.add_argument('--transport', choices=('anonymous', 'cli'), default='anonymous',
                        help='anonymous (по умолчанию) не тратит суточную квоту аккаунта')
    parser.add_argument('--allow-cli-fallback', action='store_true',
                        help='при недоступности анонимного endpoint падать на CLI (тратит квоту)')
    parser.add_argument('--interval', type=float, default=300.)
    parser.add_argument('--duration-hours', type=float, default=72.)
    parser.add_argument('--cycles', type=int)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--print-patterns', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--request-stop', type=Path, help='Папка сессии: безопасная остановка без убийства процесса')
    parser.add_argument('--stats', type=Path, help='Папка с market.sqlite3; обновить статистику без сетевых запросов')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[3]
    if args.request_stop:
        folder = args.request_stop.resolve()
        if not folder.is_relative_to(root) or not (folder/'manifest.json').is_file():
            parser.error('Нет сессии внутри проекта')
        (folder/'stop.requested').write_text(utc_now(), encoding='utf-8')
        print(json.dumps(dict(stop_requested=str(folder))))
        return 0
    if args.print_patterns:
        print(json.dumps(coverage((root/'good_cpu.txt').read_text(encoding='utf-8')), ensure_ascii=False, indent=2))
        return 0
    if args.stats:
        store = MarketStore(args.stats, read_only=True)
        try:
            print(json.dumps(store.statistics(), ensure_ascii=False, indent=2))
        finally:
            store.close()
        return 0
    if args.cycles is not None and args.cycles < 1:
        parser.error('--cycles >=1')
    cfg = MarketConfig(max_price=args.max_price, storage_gb=args.storage_gb,
        min_cores=args.min_cores, min_disk=args.min_disk, cpu_only=args.cpu_only,
        whole_machine_only=args.whole_machine_only, limit=args.limit,
        max_queries=args.max_queries, request_spacing=args.request_spacing,
        anonymous=args.transport == 'anonymous',
        allow_cli_fallback=args.allow_cli_fallback)
    monitor(root, cfg, interval=args.interval, duration_hours=args.duration_hours,
            cycles=1 if args.once else args.cycles, folder=args.output, resume=args.resume)
    return 0
