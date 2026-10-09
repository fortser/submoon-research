"""Конечновременной критерий ухода с регистрацией всех пересечений dense-полинома."""
from dataclasses import dataclass, asdict

import numpy as np
from numpy.polynomial import chebyshev as cheb

from submoon_research.dynamics.dense_segments import chebyshev_samples


def radius_crossings(dense, t0, t1, radius):
    if not t0 < t1 or not np.isfinite(radius) or radius <= 0:
        raise ValueError('Неверный интервал пересечений')
    samples, inverse = chebyshev_samples(dense, t0, t1)
    values = samples[:, -1, :3]
    if not np.isfinite(values).all():
        raise ValueError('Неверный dense output')
    coefficients = inverse @ values
    envelope = np.sum(np.linalg.norm(coefficients[1:], axis=1))
    center = np.linalg.norm(coefficients[0])
    if center-envelope > radius or center+envelope < radius:
        return []
    squared = np.zeros(2*len(coefficients)-1)
    for axis in range(3):
        part = cheb.chebmul(coefficients[:, axis], coefficients[:, axis])
        squared[:len(part)] += part
    squared[0] -= radius**2
    roots = cheb.chebroots(squared)
    events = []
    # Корень ровно на границе t0/t1 численно может выйти чуть за [-1,1].
    # Включаем его с допуском и клипуем: иначе возврат на самой границе окна
    # ухода теряется и уход подтверждается ошибочно.
    for raw in sorted(float(z.real) for z in roots
                      if abs(z.imag) < 1e-7 and -1-1e-9 <= z.real <= 1+1e-9):
        root = min(max(raw, -1.0), 1.0)
        time = t0+(root+1)*(t1-t0)/2
        state = np.asarray(dense(time))[-6:]
        residual = abs(np.linalg.norm(state[:3])-radius)
        if residual > max(1e-7, radius*1e-9):
            raise ArithmeticError('Пересечение не подтверждено dense-состоянием')
        derivative = float(cheb.chebval(root, cheb.chebder(squared)))
        if abs(derivative) < 1e-10*max(radius**2, 1):
            # Касание внутренней границы консервативно трактуется как возврат.
            direction = 0
        else:
            direction = 1 if derivative > 0 else -1
        if not events or abs(time-events[-1]['time']) > max(1e-7, 1e-12*(t1-t0)):
            events.append(dict(time=float(time), direction=direction))
    return events


@dataclass
class EscapeTracker:
    hill_km: float
    window_seconds: float
    outer_factor: float = 2.0
    pending_since: float | None = None
    last_time: float = 0.0
    outside: bool = False
    temporary_exits: int = 0
    returns: int = 0
    event: dict | None = None

    def __post_init__(self):
        if not np.isfinite([self.hill_km, self.window_seconds, self.outer_factor]).all() or self.hill_km <= 0 or self.window_seconds <= 0 or self.outer_factor < 1:
            raise ValueError('Неверный критерий ухода')

    def advance(self, dense, t0, t1):
        if t0 != self.last_time or not t0 < t1 or self.event is not None:
            raise ValueError('Разрыв истории события')
        if t0 == 0:
            state = np.asarray(dense(t0))[-6:]
            self.outside = bool(np.linalg.norm(state[:3]) >= self.hill_km)
            if np.linalg.norm(state[:3]) >= self.outer_factor*self.hill_km and np.dot(state[:3], state[3:]) > 0:
                self.pending_since = t0
        transitions = [(x['time'], 'inner', x['direction']) for x in radius_crossings(dense, t0, t1, self.hill_km)]
        transitions += [(x['time'], 'outer', x['direction']) for x in radius_crossings(dense, t0, t1, self.hill_km*self.outer_factor)]
        # На границе окна возврат имеет приоритет над подтверждением ухода.
        for time, kind, direction in sorted(transitions):
            if self.pending_since is not None and self.pending_since+self.window_seconds < time:
                return self._confirm()
            if kind == 'inner':
                if direction <= 0:
                    self.outside = False
                    self.pending_since = None
                    self.returns += 1
                else:
                    self.outside = True
                    self.temporary_exits += 1
            if kind == 'outer' and direction > 0 and self.pending_since is None:
                self.pending_since = time
        if self.pending_since is not None and self.pending_since+self.window_seconds <= t1:
            return self._confirm()
        self.last_time = float(t1)
        return None

    def _confirm(self):
        self.last_time = self.pending_since+self.window_seconds
        self.event = dict(time=self.last_time, departure_time=self.pending_since,
                          event='operational_escape', eternal_escape_proven=False)
        return self.event

    def outcome(self):
        if self.event is not None:
            return 'operational_escape'
        return 'unresolved' if self.outside or self.pending_since is not None else 'survived'

    def snapshot(self):
        return asdict(self)
