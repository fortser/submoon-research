"""W2-D043: prepare без сети; run по новому сигналу; recover только освобождает ресурс."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from submoon_research.compute.hardware_trial import Runner, TrialError, load, validate_signal  # noqa: E402
from submoon_research.compute.trial_transport import SSHProbe, VastAPI, prepare, verify_prepared  # noqa: E402
from submoon_research.tracking import current_records, read_events  # noqa: E402


def active_task(root, signal):
    records = current_records(read_events(root, 'W2'))
    record = records.get(signal['task_id'], {})
    if record.get('status') != 'active' or record.get('owner') != signal['owner']:
        raise TrialError('active_owned_task_required')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ssh-key', type=Path, default=Path.home() / '.ssh/vast_agent')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare', help='Только локальная подготовка, без сети и без аренды')
    p.add_argument('--output', type=Path)
    p = sub.add_parser('run', help='Один новый аппаратный контракт по сохранённому сигналу пользователя')
    p.add_argument('--signal', type=Path, required=True)
    p.add_argument('--prepared', type=Path, required=True)
    p = sub.add_parser('recover', help='Только reconciliation/destroy существующей попытки; create запрещён')
    p.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            output = args.output or ROOT / 'data/interim/vast_trials/prepared' / (stamp + '.json')
            value = prepare(ROOT, output, args.ssh_key)
            print(json.dumps(dict(status='prepared_offline', path=output.resolve().relative_to(ROOT).as_posix(),
                                  credential_configured=value['credential_configured'], paid_actions=0)))
            return 0
        if args.command == 'run':
            signal = validate_signal(load(args.signal))
            active_task(ROOT, signal)
            verify_prepared(ROOT, args.prepared, args.ssh_key)
        runner = Runner(ROOT, VastAPI(), SSHProbe(ROOT / 'scripts/vast_hardware_probe.py', args.ssh_key))
        result = runner.run(signal) if args.command == 'run' else runner.recover(args.run_id)
        print(json.dumps({k: result.get(k) for k in ('run_id', 'status', 'instance_id', 'error_code',
                                                   'elapsed_seconds', 'compute_disk_cost_estimate_usd',
                                                   'evidence_write_failed', 'runtime_update_failed')},
                         ensure_ascii=False))
        ok = (result['status'] == 'destroyed_verified' and not result.get('error_code')
              and not result.get('evidence_write_failed') and not result.get('runtime_update_failed'))
        return 0 if ok else 2
    except Exception as exc:
        # URL/traceback/произвольный текст чужого исключения не печатать.
        code = str(exc) if isinstance(exc, TrialError) else type(exc).__name__
        print(json.dumps(dict(status='error', error_code=code), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
