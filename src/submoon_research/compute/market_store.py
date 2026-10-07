"""SQLite — журнал наблюдений; ошибки/неполная выдача не означают отсутствие."""
from collections import Counter
import gzip
import json
from pathlib import Path
import sqlite3
import statistics


class MarketStore:
    def __init__(self, folder, read_only=False):
        self.folder = Path(folder)
        self.read_only = read_only
        if read_only:
            self.db = sqlite3.connect('file:'+str(self.folder/'market.sqlite3')+'?mode=ro', uri=True)
            return
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder/'market.sqlite3')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS cycles(id INTEGER PRIMARY KEY, utc TEXT, elapsed REAL,
 complete INTEGER, candidate_count INTEGER, summary TEXT);
CREATE TABLE IF NOT EXISTS requests(cycle_id INTEGER, sequence INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS observations(cycle_id INTEGER, offer_id INTEGER, machine_key TEXT,
 cpu_key TEXT, configuration_key TEXT, price REAL, payload TEXT, PRIMARY KEY(cycle_id,offer_id));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, cycle_id INTEGER, kind TEXT,
 offer_id INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS active(offer_id INTEGER PRIMARY KEY, first_seen TEXT, last_seen TEXT,
 missing_count INTEGER, gone INTEGER, payload TEXT);
CREATE TABLE IF NOT EXISTS targets(label TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE IF NOT EXISTS target_sightings(cycle_id INTEGER, target TEXT, PRIMARY KEY(cycle_id,target));
CREATE INDEX IF NOT EXISTS obs_cpu_cycle ON observations(cpu_key,cycle_id);
''')

    def set_targets(self, targets):
        with self.db:
            for target in targets:
                self.db.execute('INSERT OR IGNORE INTO targets VALUES(?,?)',
                    (target.label, json.dumps(target.as_dict())))

    def append(self, utc, collection, candidates, rejection_counts):
        query_summary = dict(complete=collection['complete'], incomplete=collection['incomplete'],
            queries=len(collection['queries']), errors=[r['status'] for r in collection['queries'] if r['status'] != 'ok'],
            raw_offer_count=len(collection['offers']), rejected=dict(rejection_counts))
        events = []
        with self.db:
            cursor = self.db.execute('INSERT INTO cycles(utc,elapsed,complete,candidate_count,summary) VALUES(?,?,?,?,?)',
                (utc, collection['elapsed_seconds'], int(collection['complete']), len(candidates), json.dumps(query_summary)))
            cycle = cursor.lastrowid
            for i, receipt in enumerate(collection['queries']):
                self.db.execute('INSERT INTO requests VALUES(?,?,?)', (cycle, i, json.dumps(receipt)))
            for row in candidates:
                offer = row['offer_id']
                machine = str(row['machine_id']) if row['machine_id'] is not None else 'unknown-offer-'+str(offer)
                allocation = json.dumps([row['effective_cpus_reported'], row['ram_gb_reported'],
                    row['num_gpus'], row['gpu_name'], row['observed_configuration'].get('gpu_ram'),
                    row['requested_disk_gb'], row['resource_type']], separators=(',', ':'))
                self.db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                    (cycle, offer, machine, row['cpu_key'], allocation, row['hourly_total_usd'], json.dumps(row)))
                for target in row['matched_targets']:
                    self.db.execute('INSERT OR IGNORE INTO target_sightings VALUES(?,?)', (cycle, target))
                previous = self.db.execute('SELECT first_seen,last_seen,missing_count,gone,payload FROM active WHERE offer_id=?', (offer,)).fetchone()
                kind = 'appeared' if previous is None else 'reappeared' if previous[3] else None
                old_row = json.loads(previous[4]) if previous else None
                changed_fields = []
                if previous and not kind:
                    price_fields = ('hourly_total_usd','hourly_base_usd','hourly_storage_usd')
                    config_fields = ('cpu_key','effective_cpus_reported','host_logical_cpus_reported',
                        'ram_gb_reported','num_gpus','gpu_name','resource_type','location')
                    changed_price = [f for f in price_fields if old_row.get(f) != row.get(f)]
                    changed_config = [f for f in config_fields if old_row.get(f) != row.get(f)]
                    if changed_price:
                        kind, changed_fields = 'price_changed', changed_price
                    elif changed_config:
                        kind, changed_fields = 'configuration_changed', changed_config
                if kind:
                    events.append(dict(type=kind, offer_id=offer, utc=utc, before=old_row, after=row,
                                       changed_fields=changed_fields))
                first = previous[0] if previous else utc
                self.db.execute('INSERT OR REPLACE INTO active VALUES(?,?,?,?,?,?)',
                    (offer, first, utc, 0, 0, json.dumps(row)))
            # Отсутствие интерпретируется лишь при полном успешном снимке.
            if collection['complete']:
                current = {c['offer_id'] for c in candidates}
                for offer, missing, gone, payload in self.db.execute('SELECT offer_id,missing_count,gone,payload FROM active').fetchall():
                    if offer in current or gone:
                        continue
                    missing += 1
                    self.db.execute('UPDATE active SET missing_count=?,gone=? WHERE offer_id=?',
                                    (missing, int(missing >= 2), offer))
                    if missing >= 2:
                        events.append(dict(type='not_observed_in_scope', offer_id=offer, utc=utc,
                            before=json.loads(payload), after=None,
                            interpretation='Missing from two complete polls; not proof of rental or hardware removal.'))
            for event in events:
                self.db.execute('INSERT INTO events(cycle_id,kind,offer_id,payload) VALUES(?,?,?,?)',
                                (cycle, event['type'], event['offer_id'], json.dumps(event)))
        snap_dir = self.folder/'snapshots'
        snap_dir.mkdir(exist_ok=True)
        with gzip.open(snap_dir/f'{cycle:06d}.json.gz', 'wt', encoding='utf-8') as stream:
            json.dump(dict(utc=utc, cycle_id=cycle, collection=collection, candidates=candidates),
                      stream, ensure_ascii=False, allow_nan=False)
        if events or not (self.folder/'events.jsonl').exists():
            self.export_events()
        return cycle, events

    def export_events(self):
        # Воспроизводимый экспорт из единственного источника, без дубликатов после restart.
        records = self.db.execute('SELECT id,payload FROM events ORDER BY id').fetchall()
        path = self.folder/'events.jsonl'
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            for event_id, payload in records:
                stream.write(json.dumps(json.loads(payload) | {'event_id': event_id}, ensure_ascii=False)+'\n')
        temporary.replace(path)

    def statistics(self):
        cycles = self.db.execute('SELECT id,utc,elapsed,complete,candidate_count,summary FROM cycles ORDER BY id').fetchall()
        complete = [c for c in cycles if c[3]]
        statuses = Counter()
        for row, in self.db.execute('SELECT payload FROM requests'):
            statuses[json.loads(row)['status']] += 1
        models = {}
        for key, in self.db.execute('SELECT DISTINCT cpu_key FROM observations'):
            observed = self.db.execute('''SELECT o.cycle_id,o.machine_key,o.configuration_key,o.price
                FROM observations o JOIN cycles c ON c.id=o.cycle_id WHERE o.cpu_key=? AND c.complete=1''', (key,)).fetchall()
            if not observed:
                models[key] = dict(complete_poll_sightings=0, provisional_only=True)
                continue
            # На каждом poll — одна цена на machine/allocation, а не каждый GPU offer ID.
            minima = {}
            for cycle, machine, allocation, price in observed:
                k = cycle, machine, allocation
                minima[k] = min(minima.get(k, price), price)
            prices = list(minima.values())
            seen_polls = {r[0] for r in observed}
            models[key] = dict(complete_poll_sightings=len(seen_polls),
                availability_fraction_of_complete_polls=len(seen_polls)/len(complete) if complete else None,
                unique_machines=len({r[1] for r in observed}),
                unique_allocations=len({(r[1],r[2]) for r in observed}),
                price_samples_after_machine_allocation_dedup=len(prices),
                min_hourly_usd=min(prices), median_hourly_usd=statistics.median(prices),
                max_hourly_usd=max(prices))
        target_coverage = {}
        for label, in self.db.execute('SELECT label FROM targets ORDER BY label'):
            seen = self.db.execute('''SELECT count(*) FROM target_sightings t JOIN cycles c ON t.cycle_id=c.id
                                     WHERE t.target=? AND c.complete=1''', (label,)).fetchone()[0]
            target_coverage[label] = dict(complete_polls_seen=seen,
                fraction_of_complete_polls=seen/len(complete) if complete else None)
        return dict(total_polls=len(cycles), complete_polls=len(complete), target_coverage=target_coverage,
            incomplete_or_error_polls=len(cycles)-len(complete), request_status_counts=dict(statuses),
            first_utc=cycles[0][1] if cycles else None, last_utc=cycles[-1][1] if cycles else None,
            models=models, exact_station_availability_proven=False,
            limitations=['Цена условна на 20 GB либо другой зафиксированный диск и область фильтров',
                'Опрос не видит предложения между снимками; ошибки/неполные снимки исключены из знаменателя',
                'Рыночный backend может ограничивать выдачу; полнота относится к проверяемым запросам',
                'Конфигурация заявлена провайдером, фактический CPU quota/скорость не измерены'])

    def close(self):
        if not self.read_only:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.db.close()
