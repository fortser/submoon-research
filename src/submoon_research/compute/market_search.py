"""Read-only CLI, явная полнота поиска, квоты и безопасная диагностика ошибок."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time


# Публичная техническая конфигурация; отсутствующие сведения остаются null.
PUBLIC_FIELDS = ('id', 'ask_contract_id', 'machine_id', 'resource_type', 'cpu_name',
    'cpu_arch', 'cpu_cores', 'cpu_cores_effective', 'cpu_ram', 'cpu_ghz', 'has_avx',
    'mobo_name', 'num_gpus', 'gpu_name', 'gpu_ram', 'gpu_total_ram', 'gpu_frac',
    'gpu_mem_bw', 'pci_gen', 'pcie_bw', 'disk_space', 'disk_bw', 'disk_name',
    'storage_cost', 'storage_total_cost', 'dph_base', 'dph_total', 'min_bid',
    'inet_down', 'inet_up', 'inet_down_cost', 'inet_up_cost',
    'internet_down_cost_per_tb', 'internet_up_cost_per_tb', 'direct_port_count',
    'geolocation', 'reliability', 'reliability2', 'verification', 'vericode',
    'rentable', 'rented', 'duration', 'end_date', 'driver_version', 'os_version',
    'vms_enabled', 'datacenter', 'external')


@dataclass(frozen=True)
class MarketConfig:
    max_price: float = 1.0
    storage_gb: float = 20.0
    min_cores: float = 1.0
    min_disk: float = 20.0
    cpu_only: bool = False
    whole_machine_only: bool = False
    limit: int = 1000
    max_queries: int = 24
    request_spacing: float = 5.0
    cycle_budget: float = 240.0
    timeout: float = 60.0

    def validate(self):
        for value in (self.max_price, self.storage_gb, self.min_cores, self.min_disk,
                      self.cycle_budget, self.timeout):
            if not math.isfinite(value) or value <= 0:
                raise ValueError('Ограничения должны быть положительными и конечными')
        if not 1 <= self.limit <= 1000 or not 1 <= self.max_queries <= 64 or self.request_spacing < 0:
            raise ValueError('Неверный бюджет запросов')


class SearchError(RuntimeError):
    def __init__(self, kind, status=None):
        self.kind, self.status = kind, status
        super().__init__(kind)


def parse_response(proc):
    output = proc.stdout or ''
    diagnostic = output+'\n'+(proc.stderr or '')
    match = re.search(r'(?:failed with error|HTTP(?: error)?|status(?: code)?)\s*[:=]?\s*(\d{3})', diagnostic, re.I)
    status = int(match.group(1)) if match else None
    if status == 429 or re.search(r'Too Many Requests|rate.limit', diagnostic, re.I):
        raise SearchError('rate_limited', 429)
    if status in (401, 403):
        raise SearchError('authentication_or_access_denied', status)
    if proc.returncode or status or re.search(r'Unrecognized field|Warning:|Traceback|failed with error', diagnostic):
        raise SearchError('cli_or_api_error', status)
    try:
        def reject_constant(value):
            raise ValueError('Nonfinite JSON constant')
        data, end = json.JSONDecoder(parse_constant=reject_constant).raw_decode(output.strip())
        tail = output.strip()[end:].strip()
        if tail not in ('', 'null'):
            raise ValueError()
    except (ValueError, TypeError):
        raise SearchError('invalid_json', status) from None
    if isinstance(data, dict):
        if data.get('success') is False or 'error' in data:
            raise SearchError('api_error', status)
        data = data.get('offers')
    if not isinstance(data, list) or any(not isinstance(o, dict) for o in data):
        raise SearchError('invalid_schema', status)
    return [{k: o.get(k) for k in PUBLIC_FIELDS} for o in data]


def cli_request(query, config):
    env = {k: v for k, v in os.environ.items() if k.upper() not in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')}
    env['PYTHONIOENCODING'] = 'utf-8'
    cli_path = shutil.which('vastai') or 'vastai'
    launcher = Path(cli_path)
    base_python = launcher.parent.parent/'python.exe'
    # На Windows запускаем тот же CLI без .exe-обёртки: timeout завершает
    # настоящий Python запроса, не оставляет дочерний процесс launcher.
    prefix = ([str(base_python), '-m', 'vast'] if launcher.suffix.lower() == '.exe'
              and base_python.is_file() and (launcher.parent.parent/'Lib/site-packages/vast.py').is_file()
              else [cli_path])
    command = [*prefix, 'search', 'offers', query,
        '--no-default', '--type', 'on-demand', '--storage', str(config.storage_gb),
        # CLI 0.3.1 принимает --retry строкой, затем range() падает до запроса.
        # Не передавать этот флаг: штатный int default и внешний timeout/backoff.
        '--limit', str(config.limit), '--order', 'dph_total', '--raw']
    try:
        proc = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', timeout=config.timeout, env=env)
    except subprocess.TimeoutExpired:
        raise SearchError('timeout') from None
    except OSError:
        raise SearchError('cli_unavailable') from None
    return parse_response(proc)


def collect(config, request=cli_request, clock=time.monotonic, sleep=time.sleep):
    config.validate()
    started = clock()
    offers, receipts = {}, []
    incomplete = []
    last_request = None

    def search(low, high, gpu_filter, depth=0):
        nonlocal last_request
        if any(r['status'] in ('rate_limited', 'authentication_or_access_denied') for r in receipts):
            incomplete.append(dict(low=low, high=high, gpu_filter=gpu_filter, reason='paused_after_access_error'))
            return
        if len(receipts) >= config.max_queries or clock()-started >= config.cycle_budget:
            incomplete.append(dict(low=low, high=high, gpu_filter=gpu_filter, reason='request_or_time_budget'))
            return
        if last_request is not None:
            sleep(max(0, config.request_spacing-(clock()-last_request)))
        remaining = config.cycle_budget-(clock()-started)
        if remaining <= 0:
            incomplete.append(dict(low=low, high=high, gpu_filter=gpu_filter, reason='time_budget'))
            return
        upper = '<=' if high == config.max_price else '<'
        query = (f'{gpu_filter} rentable=true rented=false cpu_arch=amd64 '
                 f'cpu_cores_effective>={config.min_cores:g} disk_space>={config.min_disk:g} '
                 f'dph_total>={low:.12g} dph_total{upper}{high:.12g}')
        last_request = clock()
        receipt = dict(query=query, started_monotonic_seconds=last_request-started)
        try:
            from dataclasses import replace
            result = request(query, replace(config, timeout=min(config.timeout, remaining)))
            receipt.update(status='ok', reported_http_status=None, count=len(result),
                response_sha256=hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest())
        except SearchError as exc:
            receipt.update(status=exc.kind, reported_http_status=exc.status, count=None)
            incomplete.append(dict(low=low, high=high, gpu_filter=gpu_filter, reason=exc.kind))
            receipts.append(receipt | {'elapsed_seconds': clock()-last_request})
            return
        receipt['elapsed_seconds'] = clock()-last_request
        receipts.append(receipt)
        for offer in result:
            if isinstance(offer.get('id'), int):
                offers[offer['id']] = offer
        if len(result) >= config.limit:
            # CLI не имеет offset: разбиение диапазона вместо "все=первые N".
            if depth < 12 and high-low > 1e-8:
                middle = (low+high)/2
                search(low, middle, gpu_filter, depth+1)
                search(middle, high, gpu_filter, depth+1)
            else:
                gpu_start = re.fullmatch(r'num_gpus>=(\d+)', gpu_filter)
                if gpu_start and int(gpu_start.group(1)) < 64:
                    first = int(gpu_start.group(1))
                    search(low, high, f'num_gpus={first}', depth+1)
                    search(low, high, f'num_gpus>={first+1}', depth+1)
                else:
                    incomplete.append(dict(low=low, high=high, gpu_filter=gpu_filter, reason='saturated_price_bucket'))

    search(0., config.max_price, 'num_gpus=0')
    if not config.cpu_only and not any(r['status'] in ('rate_limited', 'authentication_or_access_denied') for r in receipts):
        search(0., config.max_price, 'num_gpus>=1')
    if any(r['status'] != 'ok' for r in receipts):
        incomplete.append(dict(reason='request_errors'))
    return dict(offers=list(offers.values()), queries=receipts, complete=not incomplete,
                incomplete=incomplete, elapsed_seconds=clock()-started)


def rejection(offer, targets, config):
    kind = offer.get('resource_type')
    if kind not in ('cpu', 'compute', 'gpu'):
        return 'disk_or_unknown_resource'
    if kind == 'gpu' and not (isinstance(offer.get('num_gpus'), (int, float)) and offer['num_gpus'] > 0):
        return 'invalid_compute_gpu_count'
    if config.cpu_only and offer.get('num_gpus') != 0:
        return 'gpu_excluded_by_scope'
    if not any(t.matches(offer.get('cpu_name')) for t in targets):
        return 'cpu_not_in_targets'
    if offer.get('rented') is not False or offer.get('rentable') is not True:
        return 'availability_unknown_or_unavailable'
    for field, lower, upper in [('dph_total', 0., config.max_price),
                                 ('cpu_cores_effective', config.min_cores, math.inf),
                                 ('disk_space', config.min_disk, math.inf)]:
        value = offer.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lower <= value <= upper:
            return 'invalid_or_out_of_scope_'+field
    if config.whole_machine_only:
        total = offer.get('cpu_cores')
        if not isinstance(total, (int, float)) or abs(total-offer['cpu_cores_effective']) > .01:
            return 'shared_cpu_excluded_by_scope'
    return None


def candidate(offer, targets, config):
    from submoon_research.compute.cpu_targets import cpu_key, normalize_cpu
    raw_ram = offer.get('cpu_ram')
    # CLI v0.3.1: RAM отображается /1000, не /1024. Ноль неизвестен.
    ram = raw_ram/1000 if isinstance(raw_ram, (int, float)) and raw_ram > 0 else None
    total, effective = offer.get('cpu_cores'), offer.get('cpu_cores_effective')
    matching = [t for t in targets if t.matches(offer.get('cpu_name'))]
    exact = [t for t in matching if not t.family]
    canonical_cpu = normalize_cpu(exact[0].label).replace('-', ' ') if exact else cpu_key(offer.get('cpu_name'))
    return dict(offer_id=offer['id'], machine_id=offer.get('machine_id'),
        cpu_key=canonical_cpu, cpu_name_reported=offer.get('cpu_name'),
        matched_targets=[t.label for t in matching],
        hourly_total_usd=offer['dph_total'], hourly_base_usd=offer.get('dph_base'),
        hourly_storage_usd=offer.get('storage_total_cost'), requested_disk_gb=config.storage_gb,
        requested_storage_gib=config.storage_gb, storage_argument_unit='GiB_as_documented_by_CLI',
        hourly_per_effective_cpu_usd=offer['dph_total']/effective,
        effective_cpus_reported=effective, host_logical_cpus_reported=total,
        cpu_share_reported=effective/total if isinstance(total, (int, float)) and total > 0 else None,
        ram_gb_reported=ram, cpu_ram_raw=raw_ram, physical_cores=None,
        resource_type=offer.get('resource_type'), num_gpus=offer.get('num_gpus'),
        gpu_name=offer.get('gpu_name'), disk_available_gb_reported=offer.get('disk_space'),
        location=offer.get('geolocation'), verification=offer.get('verification'),
        reliability_reported=offer.get('reliability'), observed_configuration=offer,
        hardware_validation='provider_advertisement_not_leased_or_benchmarked')
