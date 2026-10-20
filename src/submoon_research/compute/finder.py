"""Поиск станций Vast.ai под профиль задачи (W2-D023, W2-T017).

Профиль (configs/compute/profiles/<имя>.yaml) задаёт жёсткие требования к машине и,
при необходимости, модель нагрузки. Предложения собираются бесплатным поиском до насыщения
(sweep_collection: несколько проходов разной формы с фильтрами наличия,
без гарантии полноты; W2-R020, W2-D025). Каждое предложение проверяется по каталогу скорости CPU
(configs/compute/cpu_catalog.yaml) и получает оценку часов и долларов на серию:

    T = setup + max(T_crit / s, CH / (s * N * e)),   C = T * $/ч

s — скорость ядра по каталогу, N — физические ядра доступной доли (для гибридных Intel —
P-ядра и, по профилю, E-ядра с их скоростью), e — параллельная эффективность.
Это сведения провайдера и оценки, не замер: фактическое железо проверяет базовая
проверка после аренды. Сетевые запросы — только чтение; аренды здесь нет.
"""
from collections import Counter
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

import yaml

from submoon_research.compute.cpu_targets import cpu_key, normalize_cpu
from submoon_research.compute.market_search import (
    PUBLIC_FIELDS, MarketConfig, recheck as _recheck, scan_market,
)

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT/'configs/compute/cpu_catalog.yaml'
SEARCH_MAX_PRICE = 1.0      # верхняя граница окон цены (база), $/ч — предел ПОИСКА, не аренды
SWEEP_MAX_SECONDS = 180.
SWEEP_MAX_QUERIES = 90
VCPU_BANDS = (24, 48, 96)    # границы диапазонов vCPU для прохода по диапазонам
PROFILES = ROOT/'configs/compute/profiles'
VERIFICATIONS = ('verified', 'unverified', 'deverified')
# W2-D029: старые профили/approval не могут вернуть deverified в новый выбор.
SEARCH_VERIFICATIONS = ('verified', 'unverified')

REQUIREMENT_DEFAULTS: dict[str, Any] = dict(
    min_effective_cpus=1.0, min_share=0.0, min_ram_gb=0.0, disk_gb=20.0, max_hourly_usd=1.0,
    min_reliability=0.0, verification=list(SEARCH_VERIFICATIONS), min_direct_ports=0,
    require_known_cpu=False, min_core_speed=0.0, cpu_regex=None, allow_gpu=True,
    max_hours=None)
WORKLOAD_DEFAULTS: dict[str, Any] = dict(core_hours=None, critical_path_hours=None, parallel_efficiency=0.8,
                         setup_hours=1.0, ram_gb_per_process=None, use_e_cores=False)
RANKS = ('cost', 'hours', 'ref_core_price', 'price_per_core')
CATALOG_FIELDS = {'key', 'match', 'speed', 'source', 'ref', 'confidence', 'note', 'smt', 'hybrid'}
SPEED_MARK = {'measured': '', 'model': '*', 'family': '~'}

REJECTION_LABELS = {
    'disk_contract': 'дисковый контракт, не вычислительная станция',
    'unavailable': 'не сдаётся или уже арендовано',
    'architecture': 'неподходящая или неизвестная архитектура CPU',
    'gpu_not_allowed': 'GPU-инстанс, а профиль разрешает только CPU',
    'price': 'дороже предела $/ч',
    'vcpu': 'меньше доступных vCPU, чем нужно',
    'share': 'срез машины меньше требуемой доли',
    'ram': 'мало RAM',
    'disk': 'мало диска',
    'reliability': 'низкая надёжность',
    'verification': 'статус проверки не разрешён профилем',
    'direct_ports': 'нет прямых портов для SSH',
    'cpu_regex': 'CPU не подходит под шаблон профиля',
    'unknown_cpu': 'CPU нет в каталоге скорости',
    'slow_cpu': 'ядро медленнее порога профиля',
}
FLAG_LABELS: dict[str, str] = {
    'deverified': 'deverified', 'unverified': 'unverified', 'slice': 'срез машины',
    'speed_estimate': 'скорость — оценка', 'family_estimate': 'оценка по семейству',
    'over_deadline': 'не укладывается в срок', 'ram_limits_workers': 'RAM ограничивает процессы',
    'low_reliability': 'надёжность < 0.97', 'unknown_cpu': 'CPU нет в каталоге',
}


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _range(value, name):
    if value is None:
        return None
    pair = list(value) if isinstance(value, (list, tuple)) else [value, value]
    if len(pair) != 2 or any(_num(v) is None or v < 0 for v in pair) or pair[0] > pair[1]:
        raise ValueError(f'{name}: ожидается число или [мин, макс]')
    return float(pair[0]), float(pair[1])


class Catalog:
    """Скорость ядра и устройство CPU; первая подходящая запись побеждает."""

    def __init__(self, data):
        self.reference = data.get('reference')
        self.entries = []
        for raw in data.get('models') or []:
            entry = dict(raw)
            extra = set(entry) - CATALOG_FIELDS
            if extra:
                raise ValueError(f'Каталог CPU: неизвестные поля {sorted(map(str, extra))} в {raw}')
            missing = {'key', 'match', 'speed', 'source'} - set(entry)
            if missing:
                raise ValueError(f'Каталог CPU: нет полей {sorted(missing)} в {raw}')
            if _num(entry['speed']) is None or entry['speed'] <= 0:
                raise ValueError('Каталог CPU: скорость должна быть > 0: '+entry['key'])
            if entry['source'] not in ('measured', 'estimate'):
                raise ValueError('Каталог CPU: source = measured|estimate: '+entry['key'])
            hybrid = entry.get('hybrid')
            if hybrid and {'p', 'e', 'threads', 'e_speed'} - set(hybrid):
                raise ValueError('Каталог CPU: hybrid требует p, e, threads, e_speed: '+entry['key'])
            entry.setdefault('smt', True)
            entry.setdefault('confidence', 'measured' if entry['source'] == 'measured' else 'model')
            entry['regex'] = re.compile(entry['match'])
            self.entries.append(entry)
        if not self.entries:
            raise ValueError('Каталог CPU пуст')

    @classmethod
    def load(cls, path=CATALOG):
        return cls(yaml.safe_load(Path(path).read_text(encoding='utf-8')))

    def lookup(self, cpu_name) -> Any:
        name = normalize_cpu(cpu_name)
        for entry in self.entries:
            if entry['regex'].search(name):
                return entry
        return None


def profile_path(name):
    path = Path(name)
    if path.suffix in ('.yaml', '.yml') and path.is_file():
        return path
    return PROFILES/f'{name}.yaml'


def list_profiles():
    return sorted(p.stem for p in PROFILES.glob('*.yaml'))


def make_profile(data, name='custom', overrides=None) -> dict[str, Any]:
    requirements = dict(REQUIREMENT_DEFAULTS)
    given = dict(data.get('requirements') or {})
    given.update(overrides or {})
    unknown = set(given) - set(REQUIREMENT_DEFAULTS)
    if unknown:
        raise ValueError(f'Профиль {name}: неизвестные требования {sorted(unknown)}')
    requirements.update(given)
    for key in ('min_effective_cpus', 'min_share', 'min_ram_gb', 'disk_gb', 'max_hourly_usd',
                'min_reliability', 'min_core_speed'):
        if _num(requirements[key]) is None or requirements[key] < 0:
            raise ValueError(f'Профиль {name}: {key} должно быть числом >= 0')
    if requirements['max_hourly_usd'] <= 0 or requirements['disk_gb'] <= 0:
        raise ValueError(f'Профиль {name}: max_hourly_usd и disk_gb должны быть > 0')
    if not set(requirements['verification']) <= set(VERIFICATIONS) or not requirements['verification']:
        raise ValueError(f'Профиль {name}: verification из {VERIFICATIONS}')
    if requirements['cpu_regex']:
        re.compile(requirements['cpu_regex'])
    workload = None
    if data.get('workload'):
        unknown = set(data['workload']) - set(WORKLOAD_DEFAULTS)
        if unknown:
            raise ValueError(f'Профиль {name}: неизвестные поля нагрузки {sorted(unknown)}')
        workload = dict(WORKLOAD_DEFAULTS, **data['workload'])
        workload['core_hours'] = _range(workload['core_hours'], 'core_hours')
        workload['critical_path_hours'] = _range(workload['critical_path_hours'], 'critical_path_hours')
        if not workload['core_hours'] and not workload['critical_path_hours']:
            raise ValueError(f'Профиль {name}: нагрузка требует core_hours и/или critical_path_hours')
        if not 0 < float(workload['parallel_efficiency']) <= 1:
            raise ValueError(f'Профиль {name}: parallel_efficiency в (0, 1]')
    rank_by = data.get('rank_by') or ('cost' if workload else 'ref_core_price')
    if rank_by not in RANKS or (rank_by in ('cost', 'hours') and not workload):
        raise ValueError(f'Профиль {name}: rank_by из {RANKS}; cost/hours требуют workload')
    return dict(name=name, title=data.get('title') or name, source=data.get('source'),
                requirements=requirements, workload=workload, rank_by=rank_by)


def load_profile(name, overrides=None):
    path = profile_path(name)
    if not path.is_file():
        raise ValueError(f'Нет профиля {name}; доступны: {", ".join(list_profiles())}')
    data = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
    return make_profile(data, path.stem, overrides)


def server_query(requirements):
    """Что уходит на сервер (W2-D025): проходы с фильтрами наличия, остальное — клиент."""
    return ('проходы до насыщения: rentable=true rented=false cpu_arch=amd64 '
            f"cpu_cores_effective>={float(requirements['min_effective_cpus']):g} "
            f"disk_space>={float(requirements['disk_gb']):g} + окна цены / диапазоны vCPU / число GPU; "
            'остальные условия — на клиенте')


def price_for_storage(offer, storage_gb):
    """$/ч при заданном диске: dph_base + storage_cost($/ГБ·мес)·ГБ/720 (сверено с dph_total)."""
    base, per_month = _num(offer.get('dph_base')), _num(offer.get('storage_cost'))
    if base is None or per_month is None:
        return _num(offer.get('dph_total'))
    return base+per_month*float(storage_gb)/720.


def cpu_label(name):
    """Короткое имя CPU для таблиц: модель, если распознана, иначе имя без мусора."""
    key = cpu_key(name)
    text = normalize_cpu(name)
    if key != text:
        return key
    text = re.sub(r'\b(?:amd|intel|processor|cpu|\d+th gen)\b|\d+-core|@.*$|with .*$|w/ .*$', ' ', text)
    return re.sub(r'\s+', ' ', text).strip() or (name or '')


def describe(offer, catalog, storage_gb=None):
    """Единая запись предложения: заявленная конфигурация + сведения каталога.
    Цена — при диске профиля (storage_gb), если он задан."""
    eff, host = _num(offer.get('cpu_cores_effective')), _num(offer.get('cpu_cores'))
    share = eff/host if eff and host else None
    raw_ram = _num(offer.get('cpu_ram'))
    price = price_for_storage(offer, storage_gb) if storage_gb else _num(offer.get('dph_total'))
    entry = catalog.lookup(offer.get('cpu_name'))
    row: dict[str, Any] = dict(offer_id=offer.get('id'), machine_id=offer.get('machine_id'),
               cpu_name=offer.get('cpu_name'), cpu_key=cpu_label(offer.get('cpu_name')),
               catalog_key=entry['key'] if entry else None,
               core_speed=float(entry['speed']) if entry else None,
               speed_source=entry['source'] if entry else None,
               speed_confidence=entry['confidence'] if entry else None,
               speed_ref=entry.get('ref') if entry else None,
               vcpu=eff, host_vcpu=host, share=share,
               ram_gb=raw_ram/1000 if raw_ram and raw_ram > 0 else None,
               disk_gb=_num(offer.get('disk_space')), num_gpus=offer.get('num_gpus'),
               gpu_name=offer.get('gpu_name'), resource_type=offer.get('resource_type'),
               usd_per_hour=price, usd_per_vcpu_hour=price/eff if price is not None and eff else None,
               verification=offer.get('verification'), reliability=_num(offer.get('reliability')),
               location=offer.get('geolocation'), direct_ports=offer.get('direct_port_count'),
               physical_cores=None, p_cores=None, e_cores=None, ref_cores=None,
               usd_per_ref_core_hour=None, flags=[])
    if entry and eff:
        hybrid = entry.get('hybrid')
        if hybrid:
            sockets = max(1, round(host/hybrid['threads'])) if host else 1
            fraction = share if share is not None else min(1., eff/(hybrid['threads']*sockets))
            row['p_cores'] = hybrid['p']*sockets*fraction
            row['e_cores'] = hybrid['e']*sockets*fraction
            row['physical_cores'] = row['p_cores']+row['e_cores']
            row['ref_cores'] = entry['speed']*(row['p_cores']+row['e_cores']*hybrid['e_speed'])
        else:
            row['physical_cores'] = eff/(2 if entry.get('smt', True) else 1)
            row['ref_cores'] = entry['speed']*row['physical_cores']
        if price is not None and row['ref_cores']:
            row['usd_per_ref_core_hour'] = price/row['ref_cores']
    return row, entry


def rejection(row, offer, entry, requirements):
    if row['resource_type'] not in ('cpu', 'compute', 'gpu'):
        return 'disk_contract'
    if row['resource_type'] == 'gpu' and not (_num(row['num_gpus']) or 0) > 0:
        return 'disk_contract'
    if offer.get('rentable') is not True or offer.get('rented') is not False:
        return 'unavailable'
    if offer.get('cpu_arch') not in ('amd64', 'x86_64'):
        return 'architecture'
    if not requirements['allow_gpu'] and (row['num_gpus'] or 0) != 0:
        return 'gpu_not_allowed'
    if row['usd_per_hour'] is None or row['usd_per_hour'] > requirements['max_hourly_usd']:
        return 'price'
    if row['vcpu'] is None or row['vcpu'] < requirements['min_effective_cpus']:
        return 'vcpu'
    if requirements['min_share'] > 0 and (row['share'] is None or row['share'] < requirements['min_share']-1e-9):
        return 'share'
    if requirements['min_ram_gb'] > 0 and (row['ram_gb'] or 0) < requirements['min_ram_gb']:
        return 'ram'
    if (row['disk_gb'] or 0) < requirements['disk_gb']:
        return 'disk'
    if requirements['min_reliability'] > 0 and (row['reliability'] or 0) < requirements['min_reliability']:
        return 'reliability'
    if (row['verification'] not in SEARCH_VERIFICATIONS
            or row['verification'] not in requirements['verification']):
        return 'verification'
    if int(requirements['min_direct_ports']) > 0 and (_num(row['direct_ports']) or 0) < int(requirements['min_direct_ports']):
        return 'direct_ports'
    if requirements['cpu_regex'] and not re.search(requirements['cpu_regex'], row['cpu_name'] or '', re.I):
        return 'cpu_regex'
    if entry is None and (requirements['require_known_cpu'] or requirements['min_core_speed'] > 0):
        return 'unknown_cpu'
    if entry is not None and entry['speed'] < requirements['min_core_speed']:
        return 'slow_cpu'
    return None


def estimate(row, entry, workload) -> dict[str, Any] | None:
    """Часы и доллары на серию; диапазон из диапазонов нагрузки профиля."""
    speed = float(entry['speed'])
    hybrid = entry.get('hybrid')
    if hybrid:
        use_e = bool(workload['use_e_cores'])
        workers = row['p_cores']+(row['e_cores'] if use_e else 0)
        throughput = speed*(row['p_cores']+(row['e_cores']*hybrid['e_speed'] if use_e else 0))
    else:
        workers = row['physical_cores']
        throughput = speed*row['physical_cores']
    limited = False
    if workload['ram_gb_per_process'] and row['ram_gb']:
        allowed = math.floor(row['ram_gb']/float(workload['ram_gb_per_process']))
        if allowed < workers:
            throughput *= max(allowed, 0)/workers if workers else 0
            workers, limited = allowed, True
    if throughput <= 0 or workers < 1:
        return None
    efficiency = float(workload['parallel_efficiency'])
    hours, bound = [], None
    for index in (0, 1):
        path = workload['critical_path_hours'][index]/speed if workload['critical_path_hours'] else 0.
        volume = workload['core_hours'][index]/(throughput*efficiency) if workload['core_hours'] else 0.
        hours.append(float(workload['setup_hours'])+max(path, volume))
        if index == 1:
            bound = 'path' if path >= volume else 'volume'
    price = row['usd_per_hour']
    return dict(hours=hours, usd=[h*price for h in hours], bound=bound, workers=workers,
                throughput_ref_cores=throughput, ram_limits_workers=limited)


def evaluate(offer, profile, catalog):
    row, entry = describe(offer, catalog, profile['requirements']['disk_gb'])
    reason = rejection(row, offer, entry, profile['requirements'])
    if reason:
        return row, reason
    flags = row['flags']
    if row['verification'] in ('deverified', 'unverified'):
        flags.append(row['verification'])
    if row['share'] is not None and row['share'] < 0.9:
        flags.append('slice')
    if row['reliability'] is not None and row['reliability'] < 0.97:
        flags.append('low_reliability')
    if entry is None:
        flags.append('unknown_cpu')
    elif entry['source'] == 'estimate':
        flags.append('family_estimate' if entry.get('confidence') == 'family' else 'speed_estimate')
    row['estimate'] = None
    if profile['workload'] and entry is not None and row['physical_cores']:
        row['estimate'] = estimate(row, entry, profile['workload'])
    if row['estimate']:
        if row['estimate']['ram_limits_workers']:
            flags.append('ram_limits_workers')
        limit = profile['requirements']['max_hours']
        if limit and row['estimate']['hours'][1] > float(limit):
            flags.append('over_deadline')
    return row, None


def rank_key(row, rank_by):
    est = row.get('estimate')
    late = 'over_deadline' in row['flags']
    missing = est is None and rank_by in ('cost', 'hours')
    if rank_by == 'cost' and est:
        value = est['usd'][1]
    elif rank_by == 'hours' and est:
        value = est['hours'][1]
    elif rank_by == 'ref_core_price':
        missing = row['usd_per_ref_core_hour'] is None
        value = row['usd_per_ref_core_hour'] if not missing else row['usd_per_vcpu_hour'] or math.inf
    else:
        value = row['usd_per_vcpu_hour'] if row['usd_per_vcpu_hour'] is not None else math.inf
    return (late, missing, value, -(row['reliability'] or 0), row['offer_id'] or 0)


def sweep_collection(profile, request=None, clock=None, sleep=None, max_seconds=SWEEP_MAX_SECONDS,
                     max_queries=SWEEP_MAX_QUERIES, drain_rounds=3) -> dict[str, Any]:
    """Ограниченный бесплатный поиск: каждому виду запросов оставляется бюджет.

    completed означает выполненную программу прохода, НЕ полноту endpoint.
    Пропущенный/оборванный проход не является свидетельством насыщения.
    W2-T019: доля новых считается только по предложениям, подходящим под серверный фильтр
    профиля (vCPU и диск): проход relaxed приносит в основном меньшие машины и раздувал
    знаменатель (14.10: 1.6% по всем 386 против ~11% по ~54 подходящим). Насыщение требует
    также досмотренного прохода exclude (каждое окно опустело, без drain_round_limit).
    """
    import time
    from dataclasses import replace
    from submoon_research.compute.market_search import SearchError, default_request, initial_windows
    if _num(max_seconds) is None or max_seconds <= 0 or max_queries < 1 or drain_rounds < 1:
        raise ValueError('Положительные конечные лимиты поиска обязательны')
    clock, sleep, request = clock or time.monotonic, sleep or time.sleep, request or default_request
    req = profile['requirements']
    started = clock()
    deadline = started + max_seconds
    low_vcpu = float(req['min_effective_cpus'])
    base = (f"rentable=true rented=false cpu_arch=amd64 cpu_cores_effective>={low_vcpu:g} "
            f"disk_space>={float(req['disk_gb']):g}")
    config = MarketConfig(max_price=min(SEARCH_MAX_PRICE, float(req['max_hourly_usd'])),
                          storage_gb=float(req['disk_gb']), request_spacing=1.,
                          cycle_budget=float(max_seconds), timeout=15., anonymous=True)
    offers, passes, queries, incomplete = {}, [], [], []
    fatal = None

    def relevant(offer):
        cores, disk = _num(offer.get('cpu_cores_effective')), _num(offer.get('disk_space'))
        return cores is not None and cores >= low_vcpu and disk is not None and disk >= float(req['disk_gb'])
    last_request = None

    def limited_request(query, cfg):
        nonlocal last_request, fatal
        if fatal:
            raise SearchError(fatal)
        called_at = clock()
        if last_request is not None:
            sleep(min(max(0., config.request_spacing-(clock()-last_request)), max(0., deadline-clock())))
        remaining = min(cfg.timeout-(clock()-called_at), deadline-clock())
        if remaining <= 0 or len(queries) >= max_queries:
            raise SearchError('request_or_time_budget')
        last_request = clock()
        try:
            return request(query, replace(cfg, timeout=remaining))
        except SearchError as exc:
            if exc.kind in ('rate_limited', 'authentication_or_access_denied', 'daily_quota'):
                fatal = exc.kind
            raise

    edges = [low_vcpu, *[b for b in VCPU_BANDS if b > low_vcpu]]
    band_queries = [base.replace(f'cpu_cores_effective>={low_vcpu:g}',
                    f'cpu_cores_effective>={lower:g}') +
                    (f' cpu_cores_effective<{edges[i+1]:g}' if i+1 < len(edges) else '')
                    for i, lower in enumerate(edges)]
    # Форма фильтра меняет неполную выдачу: узкий запрос не заменяет более широкий контроль.
    relaxed = (f'rentable=true rented=false cpu_arch=amd64 cpu_cores_effective>={min(low_vcpu, 16.):g} '
               f"disk_space>={min(float(req['disk_gb']), 20.):g}")
    groups = [('price', [base])]
    if relaxed != base:
        groups.append(('relaxed', [relaxed]))
    groups.extend([('exclude', []), ('vcpu_bands', band_queries),
                   ('gpu_count', [base+' num_gpus=1', base+' num_gpus>=2'])])
    for index, (name, shapes) in enumerate(groups):
        before, found, problems = len(queries), {}, []
        remaining_groups = len(groups)-index
        seconds = max(0., deadline-clock())/remaining_groups
        allowance = max(0, max_queries-len(queries))//remaining_groups
        pass_deadline, query_end = clock()+seconds, len(queries)+allowance
        if fatal or seconds <= 0 or allowance < 1:
            state = 'skipped'
            problems.append(dict(reason=fatal or 'request_or_time_budget'))
        elif name == 'exclude':
            windows = initial_windows([o.get('dph_base') for o in offers.values()], config.max_price, 30)
            for low, high in windows:
                drained = False
                for _ in range(drain_rounds):
                    if clock() >= pass_deadline or len(queries) >= query_end or fatal:
                        problems.append(dict(reason=fatal or 'request_or_time_budget'))
                        break
                    seen = sorted({o['machine_id'] for o in [*offers.values(), *found.values()]
                                   if isinstance(o.get('machine_id'), int)})
                    query = f'{base} dph_total>={low:.17g} dph_total<={high:.17g}'
                    if seen:
                        query += ' machine_id~notin=['+','.join(map(str, seen))+']'
                    stamp = clock()
                    receipt: dict[str, Any] = dict(sweep_pass=name, query=query, excluded_machines=len(seen))
                    try:
                        rows = limited_request(query, replace(config, timeout=min(config.timeout, pass_deadline-clock())))
                        receipt.update(status='ok', count=len(rows))
                    except SearchError as exc:
                        receipt.update(status=exc.kind, count=None)
                        rows = []
                        problems.append(dict(reason=exc.kind))
                    receipt['elapsed_seconds'] = clock()-stamp
                    queries.append(receipt)
                    fresh = [o for o in rows if isinstance(o.get('id'), int)
                             and o['id'] not in offers and o['id'] not in found]
                    found.update({o['id']: o for o in rows if isinstance(o.get('id'), int)})
                    if not fresh:
                        drained = True
                        break
                if not drained and not problems:
                    problems.append(dict(reason='drain_round_limit'))
                if fatal or clock() >= pass_deadline or len(queries) >= query_end:
                    break
            state = 'partial' if problems else 'completed'
        else:
            for shape_index, shape in enumerate(shapes):
                remaining_shapes = len(shapes)-shape_index
                time_left = pass_deadline-clock()
                query_left = query_end-len(queries)
                if time_left <= 0 or query_left < 1 or fatal:
                    problems.append(dict(reason=fatal or 'request_or_time_budget'))
                    break
                result = scan_market(replace(config, cycle_budget=time_left/remaining_shapes,
                                             timeout=min(config.timeout, time_left/remaining_shapes)),
                                     request=limited_request, clock=clock, sleep=lambda _: None,
                                     max_queries=max(1, query_left//remaining_shapes), base_query=shape)
                queries.extend(dict(q, sweep_pass=name) for q in result['queries'])
                problems.extend(result['incomplete'])
                found.update({o['id']: o for o in result['offers'] if isinstance(o.get('id'), int)})
            state = 'partial' if problems else 'completed'
        if len(queries) == before:
            state = 'skipped'
        fresh_ids = set(found)-set(offers)
        new = len(fresh_ids)
        new_relevant = sum(1 for k in fresh_ids if relevant(found[k]))
        offers.update(found)
        passes.append(dict(name=name, status=state, found=len(found), new=new, new_relevant=new_relevant,
                           queries=len(queries)-before, reasons=sorted({p['reason'] for p in problems})))
        incomplete.extend(dict(p, sweep_pass=name) for p in problems)
    total = sum(1 for o in offers.values() if relevant(o))
    tail_passes = passes[-2:]
    tail = (sum(p['new_relevant'] for p in tail_passes)/total if total and
            all(p['status'] == 'completed' and p['queries'] > 0 for p in tail_passes) else None)
    drained = all(p['status'] == 'completed' for p in passes if p['name'] == 'exclude')
    saturated = tail is not None and tail < 0.03 and drained and not fatal
    stop_reason = ('access_error' if fatal else 'time_budget' if clock() >= deadline else
                   'query_budget' if len(queries) >= max_queries else 'saturation_heuristic' if saturated else
                   'partial_passes' if incomplete else 'passes_finished')
    return dict(offers=list(offers.values()), queries=queries, complete=False, best_effort=True,
                incomplete=incomplete, elapsed_seconds=clock()-started, strategy='sweep', passes=passes,
                stop_reason=stop_reason, saturated=saturated, relevant_offers=total, exclude_drained=drained,
                last_two_passes_new_fraction=round(tail, 3) if tail is not None else None)


def find(profile, catalog=None, request=None, offers=None, clock=None, sleep=None, utc=None,
         max_seconds=SWEEP_MAX_SECONDS) -> dict[str, Any]:
    """Поиск под профиль: бесплатный поиск до насыщения; offers — готовый список (без сети)."""
    catalog = catalog or Catalog.load()
    source: dict[str, Any]
    if offers is not None:
        source = dict(offers=list(offers), queries=[], complete=None, incomplete=[], elapsed_seconds=0.,
                      strategy='replay', mode='replay', passes=[], last_two_passes_new_fraction=None)
    else:
        source = dict(sweep_collection(profile, request, clock, sleep, max_seconds), mode='sweep')
    rows, rejected, raw = [], Counter(), {}
    for offer in source['offers']:
        row, reason = evaluate(offer, profile, catalog)
        if reason:
            rejected[reason] += 1
            continue
        raw[row['offer_id']] = {k: offer.get(k) for k in PUBLIC_FIELDS}
        rows.append(row)
    rows.sort(key=lambda r: rank_key(r, profile['rank_by']))
    for index, row in enumerate(rows, 1):
        row['rank'] = index
    errors = [q['status'] for q in source['queries'] if q['status'] != 'ok']
    receipts = [{k: q.get(k) for k in ('sweep_pass', 'query', 'status', 'count', 'continued', 'elapsed_seconds',
                                       'excluded_machines')} for q in source['queries']]
    return dict(utc=utc or utc_now(), profile=dict(name=profile['name'], title=profile['title'],
                source=profile['source'], requirements=profile['requirements'],
                workload=profile['workload'], rank_by=profile['rank_by']),
                catalog_reference=catalog.reference, server_query=server_query(profile['requirements']),
                search=dict(mode=source['mode'], strategy=source['strategy'], complete=source['complete'],
                            passes=source['passes'], stop_reason=source.get('stop_reason', 'replay'),
                            saturated=source.get('saturated', False),
                            relevant_offers=source.get('relevant_offers'), exclude_drained=source.get('exclude_drained'),
                            last_two_passes_new_fraction=source['last_two_passes_new_fraction'],
                            queries=len(source['queries']),
                            errors=errors, incomplete=source['incomplete'],
                            elapsed_seconds=round(source['elapsed_seconds'], 1),
                            raw_offers=len(source['offers']), receipts=receipts),
                rejected=dict(rejected.most_common()), rows=rows, raw=raw)


def recheck(row_or_offer, requirements=None, request=None) -> dict[str, Any]:
    """Перед арендой: машина ещё свободна, цена не выше предела профиля."""
    offer_id = row_or_offer.get('offer_id', row_or_offer.get('id'))
    machine_id = row_or_offer.get('machine_id')
    config = MarketConfig(storage_gb=float((requirements or {}).get('disk_gb', 20.)), timeout=15.)
    result = _recheck(offer_id, machine_id, config, request=request)
    if result['status'] == 'available' and requirements:
        limits = make_profile(dict(requirements=requirements))
        fresh = result['offer']
        price = price_for_storage(fresh, limits['requirements']['disk_gb'])
        if price is None or price > float(limits['requirements']['max_hourly_usd']):
            result.update(status='price_changed', reason=f'цена {price} выше предела')
        else:
            row, reason = evaluate(fresh, limits, Catalog.load())
            if reason:
                result.update(status='requirements_changed', reason=reason)
            else:
                result['offer'] = dict(fresh, dph_total=row['usd_per_hour'])
    return result


class History:
    """Сквозная история наблюдений по машинам для всех профилей (одна база)."""

    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript('''
CREATE TABLE IF NOT EXISTS sightings(utc TEXT, profile TEXT, offer_id INTEGER, machine_id INTEGER,
 cpu TEXT, vcpu REAL, price REAL);
CREATE INDEX IF NOT EXISTS sightings_machine ON sightings(machine_id);
CREATE INDEX IF NOT EXISTS sightings_offer ON sightings(offer_id);''')

    def record(self, utc, profile, rows):
        with self.db:
            self.db.executemany('INSERT INTO sightings VALUES(?,?,?,?,?,?,?)',
                [(utc, profile, r['offer_id'], r['machine_id'], r['cpu_name'], r['vcpu'], r['usd_per_hour'])
                 for r in rows])

    def annotate(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for row in rows:
            first, = self.db.execute('SELECT min(utc) FROM sightings WHERE machine_id=?',
                                     (row['machine_id'],)).fetchone()
            seen, low, high = self.db.execute(
                'SELECT count(*), min(price), max(price) FROM sightings WHERE offer_id=?',
                (row['offer_id'],)).fetchone()
            row['history'] = dict(machine_first_seen=first, offer_runs_seen=seen,
                                  offer_price_min=low, offer_price_max=high)
        return rows

    def close(self):
        self.db.close()


def offers_document(result):
    """offers.json в формате, который читает vast_p2.rent (поля оффера + блок finder)."""
    offers = []
    for row in result['rows']:
        item = dict(result['raw'][row['offer_id']])
        item['dph_total_snapshot'] = item.get('dph_total')
        item['dph_total'] = row['usd_per_hour']   # при диске профиля
        item['usd_per_effective_core_hour'] = row['usd_per_vcpu_hour']
        item['finder'] = {k: row.get(k) for k in ('rank', 'catalog_key', 'core_speed', 'speed_source',
                          'share', 'physical_cores', 'p_cores', 'e_cores', 'estimate', 'flags')}
        offers.append(item)
    return dict(utc=result['utc'], source='vast_find', profile=result['profile']['name'],
                filter=result['server_query'], search=result['search'], offers=offers)


def _fmt(value, digits=2, empty='-'):
    if value is None:
        return empty
    return f'{value:.{digits}f}'


def _span(pair, digits):
    if not pair:
        return '-'
    low, high = pair
    return f'{low:.{digits}f}' if abs(high-low) < 10**-digits else f'{low:.{digits}f}–{high:.{digits}f}'


def console_table(result, top=25):
    lines = []
    s, p = result['search'], result['profile']
    if s['mode'] == 'sweep':
        steps = ', '.join(f"{x['name']} [{x.get('status', 'unknown')}] +{x['new']}"
                          + (f" (подходящих +{x['new_relevant']})" if 'new_relevant' in x else '') for x in s['passes'])
        tail = s['last_two_passes_new_fraction']
        head = (f"поиск до насыщения без гарантии полноты: проходы {steps}; запросов {s['queries']} "
                f"за {s['elapsed_seconds']:.0f} с; остановка: {s.get('stop_reason', 'unknown')}")
        if tail is not None:
            head += (f"; последние два выполненных прохода добавили {100*tail:.1f}% подходящих по vCPU/диску"
                     f" (из {s.get('relevant_offers', '?')})")
            if not s.get('saturated'):
                head += '; насыщения нет' + ('' if s.get('exclude_drained', True) else ' (exclude не досмотрен)')
        else:
            head += '; насыщение не оценено (неполные/пропущенные проходы)'
    else:
        head = 'воспроизведение сохранённых данных'
    lines.append(f"Профиль {p['name']}: {p['title']}")
    lines.append(f"{result['utc']} | {head} | предложений {s['raw_offers']}, подходит {len(result['rows'])}")
    if s['errors']:
        lines.append('Ошибки запросов: '+', '.join(s['errors']))
    has_est = p['workload'] is not None
    head = (f"{'#':>3} {'$ серия':>11} {'часы':>11} " if has_est else f"{'#':>3} ") + \
        f"{'CPU':<26} {'ядро':>5} {'vCPU':>9} {'RAM':>5} {'$/ч':>6} {'$/эт.я·ч':>8} {'статус':<10} {'offer':>9}  флаги"
    lines.append(head)
    for row in result['rows'][:top]:
        est = row.get('estimate')
        speed = (_fmt(row['core_speed']) + SPEED_MARK[row['speed_confidence']]) if row['core_speed'] else '?'
        vcpu = f"{row['vcpu']:.0f}/{row['host_vcpu']:.0f}" if row['vcpu'] and row['host_vcpu'] else _fmt(row['vcpu'], 0)
        flags = ', '.join(FLAG_LABELS.get(f, f) for f in row['flags'] if f not in ('speed_estimate', 'family_estimate'))
        prefix = (f"{row['rank']:>3} {_span(est['usd'], 1) if est else '-':>11} "
                  f"{_span(est['hours'], 1) if est else '-':>11} " if has_est else f"{row['rank']:>3} ")
        lines.append(prefix + f"{(row['cpu_key'] or '')[:26]:<26} {speed:>5} {vcpu:>9} {_fmt(row['ram_gb'], 0):>5} "
                     f"{_fmt(row['usd_per_hour'], 3):>6} {_fmt(row['usd_per_ref_core_hour'], 4):>8} "
                     f"{(row['verification'] or '')[:10]:<10} {row['offer_id']:>9}  {flags}")
    if len(result['rows']) > top:
        lines.append(f'... ещё {len(result["rows"])-top}; полный список — в HTML и offers.json')
    if result['rejected']:
        lines.append('Отсеяно: '+'; '.join(f'{REJECTION_LABELS.get(k, k)} — {v}' for k, v in result['rejected'].items()))
    lines.append('Скорость ядра (P-ядро i9-14900K = 1.00): без значка — замер; * — оценка модели; '
                 '~ — грубая оценка семейства (configs/compute/cpu_catalog.yaml)')
    lines.append('$/эт.я·ч — цена часа одного эталонного ядра: $/ч / (физ. ядра доли × скорость ядра)')
    return '\n'.join(lines)
