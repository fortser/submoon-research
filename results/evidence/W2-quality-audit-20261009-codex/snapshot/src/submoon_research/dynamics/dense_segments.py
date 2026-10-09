"""Непрерывные принятые шаги; полиномы не склеиваются через разрывы сегментов."""
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.polynomial import chebyshev as cheb


@lru_cache(maxsize=8)
def interpolation_basis(degree):
    if degree < 1 or degree > 16:
        raise ValueError('Неподдержанная степень dense-полинома')
    nodes = np.cos(np.arange(degree+1)*np.pi/degree)
    inverse = np.linalg.inv(cheb.chebvander(nodes, degree))
    return nodes, inverse


def chebyshev_samples(dense, t0, t1):
    degree = getattr(dense, 'degree', 7)
    nodes, inverse = interpolation_basis(degree)
    times = t0+(nodes+1)*(t1-t0)/2
    samples = np.asarray(dense(times)).T.reshape(degree+1, -1, 6)
    if not np.isfinite(samples).all():
        raise ValueError('Неконечный dense output')
    return samples, inverse


@dataclass
class PowerSegment:
    left: float
    right: float
    coefficients: np.ndarray

    @property
    def degree(self):
        return len(self.coefficients)-1

    def __call__(self, times):
        ts = np.asarray(times)
        slack = 8*np.finfo(float).eps*max(abs(self.left), abs(self.right), 1)
        if np.any(ts < self.left-slack) or np.any(ts > self.right+slack):
            raise ValueError('Экстраполяция dense-сегмента запрещена')
        u = (ts-self.left)/(self.right-self.left)
        return np.polynomial.polynomial.polyval(u, self.coefficients)


class CombinedSegment:
    """Внешний IAS15 и внутренний DOP853 на одном общем интервале."""
    degree = 9

    def __init__(self, massive, probe):
        self.massive = massive
        self.probe = probe

    def __call__(self, times):
        return np.concatenate([self.massive(times), self.probe(times)], axis=0)
