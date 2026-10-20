"""Continue an explicitly approved existing P2-2a contract; never create a rental.

Uses a separate --work directory and the checked vast_p2 scientific gates.
The supervisor is LOCAL: it cannot enforce deadlines while this PC is offline.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1] / 'src')]
import vast_p2 as vp  # noqa: E402
from submoon_research.tracking import writer_lock  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_START = vp._start_script


def request(method, path, payload=None):
    key = vp._api_key()
    if not key:
        raise RuntimeError('Vast credential unavailable')
    # Secret stays in memory and is never included in an exception or manifest.
    url = 'https://console.vast.ai/api/v0/' + path
    query = {'api_key': key}
    if method == 'GET' and path == 'instances/':
        query['owner'] = 'me'
    req = urllib.request.Request(url + '?' + urllib.parse.urlencode(query), method=method,
                                 data=None if payload is None else json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=25) as res:
            data = json.load(res)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'Vast HTTP {exc.code} ({method})') from None
    except Exception as exc:
        raise RuntimeError('Vast transport ' + type(exc).__name__) from None
    if isinstance(data, dict) and (data.get('success') is False or data.get('error')):
        raise RuntimeError('Vast API unsuccessful response')
    return data


def provider_cli(*args):
    """Bounded transport adapter. Creation and unrelated instances are forbidden."""
    iid = int(vp.load(vp.RENTAL)['instance_id'])
    if args == ('show', 'instances'):
        return request('GET', 'instances/').get('instances', [])
    if args == ('show', 'instance', iid):
        matches = [x for x in provider_cli('show', 'instances') if int(x['id']) == iid]
        if len(matches) != 1:
            raise RuntimeError('Approved instance not found')
        return matches[0]
    if args == ('ssh-url', iid):
        info = provider_cli('show', 'instance', iid)
        return f"ssh://root@{info['ssh_host']}:{int(info['ssh_port'])}"
    if args == ('destroy', 'instance', iid):
        return request('DELETE', f'instances/{iid}/', {})
    raise ValueError('Only reads and destruction of the approved existing contract are supported')


def check_plan(plan, rental):
    if (not plan.get('approved') or plan.get('approval_ref') != 'W2-D033'
            or int(plan.get('instance_id', -1)) != 56220810
            or int(rental.get('instance_id', -1)) != 56220810
            or plan.get('scenario') != 'p2_2a'
            or not 0 < float(plan['budget_usd']) <= 13
            or not 0 < float(plan['max_hours']) <= 18
            or not 0 < float(plan['run_deadline_hours']) < float(plan['max_hours'])
            or not 0 < float(plan['max_hourly_usd']) <= .611112
            or float(rental['offer']['dph_total']) > float(plan['max_hourly_usd'])
            or len(set(plan['physical_cpu_ids'])) != 32):
        raise ValueError('Continuation is outside W2-D033')
    if float(plan['max_hourly_usd']) * float(plan['max_hours']) + 1 > float(plan['budget_usd']):
        raise ValueError('Insufficient transfer reserve inside total budget')


def start_script(rental):
    plan = vp.approval()
    code = ORIGINAL_START(rental)
    verify = """python3 - <<'VERIFY_SOURCES'
import hashlib, json, pathlib
root = pathlib.Path(REMOTE_ROOT_LITERAL)
m = json.loads((root/'bundle_manifest.json').read_text())
for name, expected in m['files'].items():
    p = root/name
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != expected:
        raise SystemExit('SOURCE SHA MISMATCH: '+name)
print('All source files verified before installation:', len(m['files']))
VERIFY_SOURCES
[ "$?" -eq 0 ] || fail source-manifest
""".replace('REMOTE_ROOT_LITERAL', repr(vp.REMOTE_ROOT))
    code = code.replace('apt-get update -qq || fail apt-update', verify + 'apt-get update -qq || fail apt-update', 1)
    cpus = ','.join(map(str, plan['physical_cpu_ids']))
    marker = f'mkdir -p {vp.run_dir_of(rental)} runs/W2-P2-2a-baseline-{rental["instance_id"]}'
    assert marker in code
    code = code.replace(marker, f'taskset -pc {cpus} $$ >/dev/null || fail affinity\n' + marker, 1)
    code = code.replace('pin=none seed_rows=0', f'pin={cpus} seed_rows=0')
    return code


def configure(work):
    work = work.resolve()
    vp.WORK, vp.APPROVAL, vp.RENTAL = work, work/'approval.json', work/'rental.json'
    vp.BUNDLE, vp.MANIFEST = work/'bundle.tar.gz', work/'bundle_manifest.json'
    plan, rental = vp.approval(), vp.load(vp.RENTAL)
    check_plan(plan, rental)
    old_root = vp.REMOTE_ROOT
    vp.REMOTE_ROOT = plan['remote_root']
    vp._SETUP_BLOCK = vp._SETUP_BLOCK.replace(old_root, vp.REMOTE_ROOT)
    vp._start_script = start_script
    vp.vw.WORK = work
    vp.vw.cli = provider_cli
    return plan, rental


def snapshot():
    r, plan = vp.load(vp.RENTAL), vp.approval()
    elapsed = (time.time() - r['created_epoch']) / 3600
    value = dict(utc=vp.vw.now(), instance_id=r['instance_id'], supervisor_pid=os.getpid(),
                 elapsed_hours=elapsed, compute_disk_upper_usd=elapsed * r['offer']['dph_total'],
                 budget_usd=plan['budget_usd'], hard_deadline_epoch=r['deadline_epoch'],
                 local_supervisor_not_offline_safe=True, science_deadline_utc=r.get('run_deadline_utc'))
    info = provider_cli('show', 'instance', int(r['instance_id']))
    value['actual_status'] = info.get('actual_status')
    if info.get('actual_status') == 'running':
        with vp.vw.connection(info) as client:
            value['stage'] = vp.vw.remote_command(client, 'cat /workspace/p2_status 2>/dev/null || echo none', 15).strip()
            base = f'{vp.REMOTE_ROOT}/{vp.run_dir_of(r)}'
            for name in ('progress.json', 'baseline_gate.json'):
                raw = vp.vw.remote_command(client, f'test ! -f {base}/{name} || head -c 20000 {base}/{name}', 15)
                if raw.strip():
                    value[name[:-5]] = json.loads(raw)
    vp.save(vp.WORK/'supervisor_status.json', value)
    return value


def update_runtime(state):
    for attempt in range(10):
        try:
            with writer_lock(ROOT):
                path = ROOT/'tracking/runtime.json'
                runtime = vp.load(path)
                for item in runtime['processes']:
                    if item.get('instance_id') == state['instance_id']:
                        item.update(last_checked_utc=state['utc'], status=state.get('stage', state.get('actual_status')),
                                    supervisor_pid=os.getpid(), supervisor_heartbeat=(vp.WORK/'supervisor_status.json').relative_to(ROOT).as_posix(),
                                    deadline_epoch=state.get('hard_deadline_epoch'), budget_usd=13,
                                    note='Local supervisor; stops during PC sleep/offline. W2-D033.')
                runtime['updated_utc'] = state['utc']
                vp.save(path, runtime)
            return
        except ValueError:
            if attempt == 9:
                raise
            time.sleep(.3)


def deploy_checked():
    plan, r = vp.approval(), vp.load(vp.RENTAL)
    if vp.BUNDLE.stat().st_size > 256 * 1024**2:
        raise ValueError('Bundle exceeds the approved small transfer envelope')
    info = provider_cli('show', 'instance', int(r['instance_id']))
    with vp.vw.connection(info) as client:
        cmd = ('find /workspace -maxdepth 2 -type f -printf "%p\\n"; '
               'ps -eo pid,comm,pcpu,pmem; '
               f'test ! -e {shlex.quote(vp.REMOTE_ROOT)} && '
               'test ! -e /workspace/p2_start.sh && test ! -e /workspace/p2_status && '
               f'test ! -e /workspace/p2_{r["instance_id"]}.log && echo SUBMOON_EMPTY_TARGET')
        output = vp.vw.remote_command(client, cmd, 20)
        if not output.rstrip().endswith('SUBMOON_EMPTY_TARGET'):
            raise ValueError('Remote target is not demonstrably new; deployment refused')
        (vp.WORK/'deployment_inventory.txt').write_text(output, encoding='utf-8')
    vp.deploy()
    r = vp.load(vp.RENTAL)
    r['continuation_approval'] = plan['approval_ref']
    r['physical_cpu_ids'] = plan['physical_cpu_ids']
    vp.save(vp.RENTAL, r)


def supervise():
    # OS lock, not just PID existence, prevents two finish/destroy loops.
    from submoon_research.compute.radar import single_instance
    with single_instance(vp.WORK/'supervisor_oslock'):
        vp.save(vp.WORK/'supervisor_launch.json', dict(pid=os.getpid(), utc=vp.vw.now()))
        failure_since = None
        while True:
            try:
                r, plan = vp.load(vp.RENTAL), vp.approval()
                if r.get('status') == 'destroyed_verified':
                    return
                s = snapshot()
                update_runtime(s)
                print(json.dumps({k: s.get(k) for k in ('utc', 'stage', 'elapsed_hours', 'compute_disk_upper_usd')}), flush=True)
                failure_since = None
                stage = s.get('stage', '')
                now = time.time()
                terminal = stage.startswith(('done', 'FAILED'))
                near_end = now >= r['deadline_epoch'] - 1800
                budget_near = s['compute_disk_upper_usd'] >= plan['budget_usd'] - 1.25
                setup_stuck = stage.startswith(('setup', 'gates')) and now > r['created_epoch'] + 3600
                if terminal or near_end or budget_near or setup_stuck:
                    if stage.startswith(('running', 'baseline')):
                        vp.wrapup()
                        for _ in range(12):
                            time.sleep(10)
                            if vp.remote_stage(vp.load(vp.RENTAL)).startswith(('done', 'FAILED')):
                                break
                    vp.finish(partial=True)
                    final = dict(s, utc=vp.vw.now(), stage='destroyed_verified', actual_status='absent')
                    vp.save(vp.WORK/'supervisor_final.json', final)
                    update_runtime(final)
                    print('FINISH VERIFIED', flush=True)
                    return
            except Exception as exc:
                # Exceptions never include request URLs or credentials.
                print(vp.vw.now(), 'supervisor error', type(exc).__name__, flush=True)
                failure_since = failure_since or time.time()
                vp.save(vp.WORK/'supervisor_error.json', dict(utc=vp.vw.now(), error_type=type(exc).__name__,
                                                            failure_since=failure_since))
            time.sleep(30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'deploy', 'status', 'supervise', 'finish'))
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    configure(args.work)
    if args.action == 'prepare':
        vp.prepare()
        vp.save(vp.WORK/'transport_sha256.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                                for p in (Path(__file__), ROOT/'scripts/vast_p2.py', ROOT/'scripts/vast_w2.py')})
    elif args.action == 'deploy':
        deploy_checked()
    elif args.action == 'status':
        print(json.dumps(snapshot(), ensure_ascii=False, indent=2))
    elif args.action == 'finish':
        vp.finish(partial=True)
    else:
        supervise()


if __name__ == '__main__':
    main()
