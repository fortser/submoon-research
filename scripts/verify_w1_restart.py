"""Принудительное убийство worker после атомарного checkpoint и отдельный restart."""
import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from submoon_research.contracts.checkpoint import validate_checkpoint
from submoon_research.dynamics.baseline import integrate
from submoon_research.events.escape import EscapeTracker
from submoon_research.execution import RunContext
from submoon_research.tracking import atomic_text
from submoon_research.workflows.smoke import write_json


def worker(folder, mode):
    initial = [0., 0., 0., 0., 0., 0., 2., 0., 0., 0., math.sqrt(0.5), 0.]
    horizon = 4*2*math.pi*math.sqrt(8)
    options = dict(rtol=1e-11, atol_position=1e-10, atol_velocity=1e-12, max_step=0.1)
    cp_path = folder/'checkpoints/worker.json'
    tracker = EscapeTracker(10., 20.)
    def checkpoint(data):
        data.update(model_sha256='a'*64, orbit_id='synthetic-restart',
            input_state_sha256=hashlib.sha256(json.dumps(initial).encode()).hexdigest())
        atomic_text(cp_path, json.dumps(data))
        if mode == 'interrupt':
            # Родитель обязан завершить этот ограниченный worker; ожидание — только handshake.
            time.sleep(30)
            raise TimeoutError('Supervisor не прервал worker')
    start, t0 = initial, 0.
    if mode == 'resume':
        cp = validate_checkpoint(json.loads(cp_path.read_text()), initial=initial,
            orbit_id='synthetic-restart', model_sha256='a'*64, gms=[1.], horizon=horizon, options=options)
        start, t0 = cp['state'], cp['time']
        tracker = EscapeTracker(**cp['escape'])
    result = integrate(start, [1.], [], [0.1], horizon, **options, t0=t0,
        escape_tracker=tracker, checkpoint_time=horizon/2 if mode != 'resume' else None,
        checkpoint=checkpoint)
    write_json(folder/f'results/{mode}.json', result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', choices=['interrupt', 'resume', 'continuous'])
    parser.add_argument('--folder', type=Path)
    args = parser.parse_args()
    if args.worker:
        worker(args.folder, args.worker)
        return 0
    root = Path(__file__).resolve().parents[1]
    config = dict(experiment_id='W1-system-restart-v1', data_kind='synthetic_analytic_control', production_allowed=False)
    with RunContext(root, 'W1-restart', config, wall_seconds=120, output_mib=16) as run:
        run.save_code(Path(__file__).resolve())
        command = [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--folder', str(run.folder)]
        proc = subprocess.Popen(command+['--worker', 'interrupt'], cwd=root,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        killed = False
        try:
            deadline = time.monotonic()+20
            while not (run.folder/'checkpoints/worker.json').exists():
                run.check_budget()
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Worker не создал checkpoint')
                time.sleep(0.05)
            proc.kill()
            proc.wait(timeout=5)
            killed = proc.returncode != 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            proc.stderr.close()
        run.subprocess(command+['--worker', 'resume'], check=True, capture_output=True)
        run.subprocess(command+['--worker', 'continuous'], check=True, capture_output=True)
        resumed = json.loads((run.folder/'results/resume.json').read_text())
        normal = json.loads((run.folder/'results/continuous.json').read_text())
        checks = dict(external_kill=killed, same_final_state=resumed['final_state'] == normal['final_state'],
            same_outcome=resumed['physical_outcome'] == normal['physical_outcome'],
            same_final_time=resumed['last_valid_time'] == normal['last_valid_time'])
        validation = run.validate(checks, scope='V08_synthetic_single_worker_state_restart_not_production_supervisor')
    print(json.dumps(dict(run_id=run.run_id, status=validation['status'])))
    return int(validation['status'] != 'passed')


if __name__ == '__main__':
    raise SystemExit(main())
