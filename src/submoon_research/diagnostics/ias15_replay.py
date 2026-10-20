"""Ограниченная диагностика IAS15 без изменения сил, шага и защит адаптера.

Native snapshot — экспериментальный диагностический артефакт, НЕ production
checkpoint: здесь нет состояния EscapeTracker/полной истории исполнителя.
"""
from __future__ import annotations

import collections
import importlib.metadata
import math
import platform
import time
import warnings
from pathlib import Path

import numpy as np

from submoon_research.contracts.p2_files import atomic_json, canonical, digest, file_digest, read_json
from submoon_research.dynamics.native_ias15 import NativeIAS15, SUPPORTED_REBOUND


def environment():
    return dict(python=platform.python_version(), platform=platform.platform(),
                packages={p: importlib.metadata.version(p) for p in ('rebound', 'reboundx', 'numpy')},
                native_adapter_sha256=file_digest(Path(__file__).parents[1]/'dynamics/native_ias15.py'))


def adaptation(native):
    integ = native.sim.integrator
    return dict(epsilon=float(integ.epsilon), min_dt=float(integ.min_dt),
                adaptive_mode=str(integ.adaptive_mode),
                iterations_max_exceeded=int(integ.iterations_max_exceeded))


def guard_reason(error, left, right, bound):
    text = str(error)
    if 'Граница IAS15 должна быть впереди' in text:
        return 'bound_not_ahead'
    if 'IAS15 нарушил временную границу' in text:
        return 'no_time_advance' if right <= left else 'time_bound_exceeded'
    if 'Фактический шаг IAS15 не согласован' in text:
        return 'inconsistent_dt_done'
    if 'Полином IAS15 не воспроизвёл' in text:
        return 'endpoint_mismatch'
    return 'other_exception'


def physical_snapshot(native, figures):
    return dict(time=float(native.sim.t), dt=float(native.sim.dt), G=float(native.sim.G),
                absolute_state=native.absolute_state().tolist(),
                gms=[float(p.m*native.sim.G) for p in native.sim.particles[:-1]],
                figures=figures, adaptation=adaptation(native),
                frame='absolute state used internally by NativeIAS15; nonrotating ICRF axes',
                position_unit='km', velocity_unit='km/s', time_unit='s',
                continuation='physical_state_new_adaptive_history')


def save_snapshot(native, directory, *, figures, identity):
    """Сохранить принятый pre-step state плюс REBOUND/REBOUNDx native history."""
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    physical = physical_snapshot(native, figures)
    atomic_json(directory/'physical.json', physical)
    # Новые имена: Simulation.save_to_file по умолчанию дописывает, поэтому каталог exclusive.
    native.sim.save_to_file(str(directory/'simulation.bin'))
    native.rebx.save(str(directory/'reboundx.bin'))
    manifest = dict(schema='ias15-diagnostic-snapshot-v1', environment=environment(), identity=identity,
                    diagnostic_writer_sha256=file_digest(Path(__file__)),
                    files={n: file_digest(directory/n) for n in ('physical.json', 'simulation.bin', 'reboundx.bin')},
                    time=physical['time'], count=native.count, scope='native-step replay only; no event tracker')
    atomic_json(directory/'snapshot_manifest.json', manifest)
    return dict(manifest=str(directory/'snapshot_manifest.json'),
                sha256=file_digest(directory/'snapshot_manifest.json'), time=physical['time'])


def load_snapshot(path, expected_sha256, *, expected_identity, physical_only=False):
    path = Path(path).resolve()
    if not expected_sha256 or file_digest(path) != expected_sha256:
        raise ValueError('SHA snapshot manifest не совпал с внешним якорем')
    doc = read_json(path)
    if doc.get('schema') != 'ias15-diagnostic-snapshot-v1' or doc.get('identity') != expected_identity:
        raise ValueError('Чужой snapshot/исходный контракт')
    if doc.get('environment') != environment():
        raise ValueError('Native replay требует то же окружение и ABI')
    if set(doc['files']) != {'physical.json', 'simulation.bin', 'reboundx.bin'}:
        raise ValueError('Неполный snapshot')
    for name, expected in doc['files'].items():
        if file_digest(path.parent/name) != expected:
            raise ValueError('SHA snapshot файла не совпал: '+name)
    p = read_json(path.parent/'physical.json')
    if physical_only:
        native = NativeIAS15(p['absolute_state'], p['gms'], p['figures'],
                             t0=p['time'], initial_dt=p['dt'], epsilon=p['adaptation']['epsilon'])
    else:
        import rebound
        import reboundx
        if rebound.__version__ != SUPPORTED_REBOUND:
            raise ValueError('Неподдерживаемый native ABI')
        native = NativeIAS15.__new__(NativeIAS15)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            native.sim = rebound.Simulation(str(path.parent/'simulation.bin'))
            native.rebx = reboundx.Extras(native.sim, str(path.parent/'reboundx.bin'))
        # REBOUND сообщает о необходимости восстановить function pointers;
        # Extras это делает. Любое другое предупреждение не скрывается.
        unexpected = [str(w.message) for w in caught if 'function pointers' not in str(w.message)]
        if unexpected:
            raise ValueError('Предупреждения загрузки native snapshot: '+repr(unexpected))
        native.count = int(doc['count'])
        native.harmonics = native.rebx.get_force('gravitational_harmonics')
    if (canonical(native.absolute_state().tolist()) != canonical(p['absolute_state'])
            or native.sim.t != p['time'] or native.sim.dt != p['dt'] or native.sim.G != p['G']
            or [float(body.m*native.sim.G) for body in native.sim.particles[:-1]] != p['gms']):
        raise ValueError('Snapshot не восстановил физическое состояние')
    if not physical_only and adaptation(native) != p['adaptation']:
        raise ValueError('Не восстановлены параметры IAS15')
    # Force parameters проверяются независимо от одного лишь совпадения координат.
    for f in p['figures']:
        particle = native.sim.particles[f['index']]
        pole = list(particle.params['Omega'])
        if (particle.params['J2'] != f['j2'] or particle.params['R_eq'] != f['radius']
                or pole != f['pole']):
            raise ValueError('Не восстановлены параметры REBOUNDx')
    return native, doc


class StepTrace:
    """Кольцевой буфер только в выбранном окне; snapshot до шага, не после guard.

    Первое пересечение snapshot_at (или слишком малый предложенный dt) создаёт
    один native snapshot. Миллионы предыдущих шагов не сериализуются.
    """
    def __init__(self, *, directory, figures, identity, ring=128, trace_after=0.0,
                 snapshot_at=None, small_dt=1e-3, max_steps=10000, wall_seconds=60.0,
                 wall_limit_seconds=600):
        # Общий бюджет остаётся 600 s; повышенный предел передаёт только
        # launcher после проверки отдельно согласованного фиксированного профиля.
        if (wall_limit_seconds not in (600, 5400) or not 1 <= ring <= 1000
                or not 1 <= max_steps <= 10_000_000 or not 0 < wall_seconds <= wall_limit_seconds):
            raise ValueError('Диагностика ограничена ring<=1000 и явным wall limit (обычно 600 s)')
        thresholds = [trace_after, small_dt] + ([] if snapshot_at is None else [snapshot_at])
        if any(not math.isfinite(x) or x < 0 for x in thresholds):
            raise ValueError('Пороги диагностики должны быть конечными и неотрицательными')
        self.directory = Path(directory)
        self.figures, self.identity = figures, identity
        self.records = collections.deque(maxlen=ring)
        self.trace_after, self.snapshot_at, self.small_dt = trace_after, snapshot_at, small_dt
        self.max_steps, self.wall_seconds = max_steps, wall_seconds
        self.started = time.monotonic()
        self.steps = 0
        self.snapshot = None
        self.failure = None

    def step(self, original, native, bound, max_step=np.inf):
        if self.steps >= self.max_steps or time.monotonic()-self.started >= self.wall_seconds:
            raise TimeoutError('diagnostic_budget: остановка, не численный исход')
        left, proposed = float(native.sim.t), float(native.sim.dt)
        triggered = ((self.snapshot_at is not None and left >= self.snapshot_at)
                     or abs(proposed) <= self.small_dt)
        if triggered and self.snapshot is None:
            self.snapshot = save_snapshot(native, self.directory/'pre_step_snapshot',
                                          figures=self.figures, identity=self.identity)
        tracing = left >= self.trace_after or triggered
        record = None
        if tracing:
            state = native.absolute_state()
            distances = [float(np.linalg.norm(a[:3]-b[:3])) for i, a in enumerate(state) for b in state[i+1:]]
            record = dict(n=self.steps, left=left, proposed_dt=proposed,
                          requested_dt=min(proposed, bound-left, max_step), bound=float(bound),
                          max_step=float(max_step) if math.isfinite(max_step) else None,
                          time_ulp=float(np.spacing(left)), adaptation_before=adaptation(native),
                          min_pair_distance=min(distances), absolute_position_max=float(np.max(abs(state[:, :3]))))
            self.records.append(record)
        try:
            segment = original(native, bound, max_step)
        except Exception as exc:
            self.failure = dict(type=type(exc).__name__, message=str(exc),
                                reason=guard_reason(exc, left, float(native.sim.t), bound))
            if record is not None:
                record['failure'] = self.failure
            raise
        finally:
            if record is not None:
                record.update(right=float(native.sim.t), actual_dt=float(native.sim.t)-left,
                              dt_done=float(native.sim.dt_last_done), next_dt=float(native.sim.dt),
                              adaptation_after=adaptation(native),
                              checked_endpoint_residual=getattr(native, 'checked_endpoint_residual', None))
        if record is not None:
            record['absolute_state_sha256'] = digest(native.absolute_state().tolist())
            record['coefficients_sha256'] = digest(segment.coefficients.tolist())
        self.steps += 1
        return segment

    def report(self):
        return dict(steps=self.steps, failure=self.failure, snapshot=self.snapshot,
                    ring_capacity=self.records.maxlen, records=list(self.records),
                    wall_seconds=time.monotonic()-self.started, max_steps=self.max_steps,
                    wall_limit_seconds=self.wall_seconds, trace_after=self.trace_after,
                    snapshot_at=self.snapshot_at, small_dt=self.small_dt)
