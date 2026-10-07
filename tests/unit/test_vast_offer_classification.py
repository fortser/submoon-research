"""Защита от платной аренды дискового тома под видом CPU сервера."""
import importlib.util
from pathlib import Path


def load(name):
    p = Path(__file__).resolve().parents[2]/'scripts'/f'{name}.py'
    spec = importlib.util.spec_from_file_location(name, p)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_monitor_and_tracker_reject_real_disk_offer_shape():
    disk = dict(cpu_name='AMD EPYC 9654 96-Core Processor', num_gpus=0,
        cpu_cores_effective=384., cpu_cores=384, cpu_ram=0, disk_space=7168.,
        dph_total=.01296, resource_type='disk', rented=False)
    monitor, tracker = load('vast_monitor'), load('vast_track_cpu')
    assert not monitor.matches(disk, ['9654'], .02, 16)
    assert not tracker.matches(disk, ['EPYC 9654'])
    unknown = disk | {'resource_type': None}
    assert not tracker.matches(unknown, ['EPYC 9654'])
    compute = disk | {'resource_type': 'cpu', 'cpu_ram': 32000}
    assert monitor.matches(compute, ['9654'], .02, 16)
    assert tracker.matches(compute, ['EPYC 9654'])
