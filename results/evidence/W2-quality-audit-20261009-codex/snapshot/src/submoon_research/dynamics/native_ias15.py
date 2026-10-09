"""IAS15 5.1.1 + REBOUNDx; адаптер принятого шага с проверкой ABI."""
import bisect
import ctypes
import hashlib
import json
import time

import numpy as np

from submoon_research.dynamics.dense_segments import PowerSegment


SUPPORTED_REBOUND = '5.1.1'


class NativeIAS15:
    def __init__(self, state, gms, figures, *, epsilon=1e-12, t0=0.0, initial_dt=1e-5):
        import rebound
        import reboundx

        if rebound.__version__ != SUPPORTED_REBOUND:
            raise RuntimeError('Dense ABI требует REBOUND '+SUPPORTED_REBOUND)
        self.sim = rebound.Simulation()
        self.sim.G = 1.0
        self.count = len(np.asarray(state).reshape(-1, 6))
        for index, row in enumerate(np.asarray(state).reshape(-1, 6)):
            self.sim.add(m=float(gms[index]) if index < len(gms) else 0.0,
                         x=row[0], y=row[1], z=row[2], vx=row[3], vy=row[4], vz=row[5])
        self.sim.integrator = 'ias15'
        self.sim.integrator.epsilon = epsilon
        self.sim.integrator.adaptive_mode = 'PRS23'
        self.sim.integrator.min_dt = 0.0
        self.sim.t = t0
        self.sim.dt = initial_dt
        self.rebx = reboundx.Extras(self.sim)
        self.harmonics = self.rebx.load_force('gravitational_harmonics')
        self.rebx.add_force(self.harmonics)
        for f in figures:
            p = self.sim.particles[f['index']]
            p.params['J2'] = float(f['j2'])
            p.params['R_eq'] = float(f['radius'])
            p.params['Omega'] = rebound.Vec3d(*map(float, f['pole']))
        fields = {f.name.decode(): f for f in self.sim.integrator.callbacks.field_descriptor_list}
        for name, size in (('a0', 8), ('br', 56)):
            if name not in fields or fields[name].element_size != size:
                raise RuntimeError('Не совпал формат dense ABI: '+name)

    def _array(self, name, size):
        pointer = getattr(self.sim.integrator, name)
        if not pointer:
            raise RuntimeError('IAS15 ещё не создал буфер '+name)
        return np.ctypeslib.as_array(
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_double)), shape=(size,)).copy()

    def absolute_state(self):
        return np.array([[p.x, p.y, p.z, p.vx, p.vy, p.vz] for p in self.sim.particles])

    def _assemble(self, start, a0, b, dt):
        coeff = np.zeros((10, self.count, 6))
        coeff[0] = start
        coeff[1, :, :3] = start[:, 3:]*dt
        coeff[2, :, :3] = a0*dt*dt/2
        coeff[1, :, 3:] = a0*dt
        for k in range(7):
            coeff[k+3, :, :3] = b[k]*dt*dt/((k+2)*(k+3))
            coeff[k+2, :, 3:] = b[k]*dt/(k+2)
        return coeff

    def step(self, bound, max_step=np.inf):
        left = float(self.sim.t)
        if not bound > left:
            raise ValueError('Граница IAS15 должна быть впереди')
        start = self.absolute_state()
        self.sim.dt = min(float(self.sim.dt), bound-left, max_step)
        # REBOUND 5.1.1 не имеет Simulation.step(); steps(1) выполняет ровно
        # один шаг текущим sim.dt и оставляет br последнего шага для ABI.
        self.sim.steps(1)
        right = float(self.sim.t)
        dt = right-left
        if not dt > 0 or right > bound+8*np.spacing(bound):
            raise ArithmeticError('IAS15 нарушил временную границу')
        a0 = self._array('a0', 3*self.count).reshape(self.count, 3)
        # br = коэффициенты выполненного шага.
        b = self._array('br', 21*self.count).reshape(7, self.count, 3)
        coeff = self._assemble(start, a0, b, dt)
        end = self.absolute_state()
        residual = float(np.max(abs(coeff.sum(axis=0)-end)/(1+abs(end))))
        # 2e-13 оказалось ниже достижимой согласованности IAS15 при J2: при
        # несходимости predictor-corrector остаток растёт до ~6e-13 (проверено
        # диагностикой). 1e-9 на два порядка ниже научного порога интерполяции
        # 1e-7 и выше наблюдённого дна, поэтому ловит грубые дефекты, но не
        # аварийно завершает корректный шаг.
        if not np.isfinite(residual) or residual > 1e-9:
            raise ArithmeticError('Полином IAS15 не воспроизвёл конец принятого шага')
        coeff -= coeff[:, :1, :]
        self.last_endpoint_residual = residual
        return PowerSegment(left, right, coeff.reshape(10, -1))


class MassiveCache:
    """Ограниченный неизменяемый префикс траектории без пробного тела."""
    def __init__(self, state, gms, figures, *, epsilon=1e-12,
                 max_step=np.inf, byte_limit=128*2**20):
        self.initial = np.asarray(state).reshape(-1, 6)[:len(gms)].copy()
        self.gms = np.asarray(gms)
        self.figures = figures
        self.epsilon = epsilon
        self.max_step = max_step
        self.byte_limit = byte_limit
        self.segments = []
        self.ends = []
        self._cursor = 0
        self.native = None
        self.wall_seconds = 0.0
        self.cpu_seconds = 0.0
        self.bytes = 0
        self.identity = hashlib.sha256(json.dumps(dict(state=self.initial.tolist(),
            gms=self.gms.tolist(), figures=figures, epsilon=epsilon,
            max_step=max_step if np.isfinite(max_step) else None,
            rebound=SUPPORTED_REBOUND), sort_keys=True).encode()).hexdigest()

    @property
    def end(self):
        return self.ends[-1] if self.ends else 0.0

    def extend(self, bound, budget, *, split_grid=()):
        started, cpu = time.perf_counter(), time.process_time()
        try:
            if self.native is None:
                seed = self.segments[-1](self.end) if self.segments else self.initial
                self.native = NativeIAS15(seed, self.gms, self.figures,
                    epsilon=self.epsilon, t0=self.end)
            for stop in [*sorted(t for t in split_grid if self.end < t < bound), bound]:
                while self.end < stop:
                    budget()
                    segment = self.native.step(stop, self.max_step)
                    if self.bytes+segment.coefficients.nbytes > self.byte_limit:
                        raise MemoryError('Лимит внешнего кеша')
                    self.segments.append(segment)
                    self.ends.append(segment.right)
                    self.bytes += segment.coefficients.nbytes
        finally:
            self.wall_seconds += time.perf_counter()-started
            self.cpu_seconds += time.process_time()-cpu

    def segment_at(self, t):
        # Семантика прежнего np.searchsorted(self.ends, t, side='right'), но без
        # преобразования всего списка границ в массив на каждом вызове (O(n)):
        # сначала проверяется сегмент предыдущего обращения (последовательные
        # вызовы RHS/dense попадают в него), иначе bisect по списку, O(log n).
        # Курсор только ускоряет поиск: он проверяется по тем же границам и не
        # предполагает монотонного времени.
        ends, count, index = self.ends, len(self.ends), self._cursor
        if not (index < count and t < ends[index] and (index == 0 or ends[index-1] <= t)):
            index = bisect.bisect_right(ends, t)
            if index == count and count and t == ends[-1]:
                index -= 1
            if index < count:
                self._cursor = index
        if index >= len(self.segments) or t < self.segments[index].left:
            raise ValueError('Состояние вне покрытия кеша')
        return self.segments[index]

    def __call__(self, t):
        return self.segment_at(t)(t)

    def save(self, path):
        if not self.segments:
            raise ValueError('Пустой кеш не сохраняется')
        np.savez_compressed(path, coefficients=np.stack([s.coefficients for s in self.segments]),
            bounds=np.array([[s.left, s.right] for s in self.segments]),
            identity=np.array(self.identity), epsilon=self.epsilon)

    def load(self, path, expected_sha256):
        from submoon_research.workflows.smoke import sha256
        if sha256(path) != expected_sha256:
            raise ValueError('SHA кеша не совпал')
        with np.load(path, allow_pickle=False) as saved:
            if str(saved['identity']) != self.identity:
                raise ValueError('Идентификатор кеша не совпал')
            bounds, coeff = saved['bounds'], saved['coefficients']
            if (len(bounds) != len(coeff) or not len(bounds) or bounds[0, 0] != 0
                    or not np.isfinite(bounds).all() or not np.isfinite(coeff).all()
                    or np.any(bounds[:, 1] <= bounds[:, 0])
                    or np.any(bounds[1:, 0] != bounds[:-1, 1])):
                raise ValueError('Нарушен контракт сегментов кеша')
            if coeff.nbytes > self.byte_limit:
                raise MemoryError('Загружаемый кеш превышает лимит')
            self.segments = [PowerSegment(float(b[0]), float(b[1]), c.copy()) for b, c in zip(bounds, coeff)]
            self.ends = bounds[:, 1].tolist()
            self._cursor = 0
            self.bytes = coeff.nbytes
            self.native = None
