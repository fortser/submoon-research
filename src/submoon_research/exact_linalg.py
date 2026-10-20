"""Малые векторы и матрицы без BLAS: результат не зависит от процессора (W2-I035).

np.dot, np.linalg.norm и @ идут через OpenBLAS, который выбирает ядро по процессору
(AVX-512 — SkylakeX, AVX2 — Haswell …); порядок суммирования в ядрах разный, и результат
отличается в последних битах. В P2-2a это дало другие стартовые состояния всех единиц Япета
на i5-9400 по сравнению с сервером (2 ULP в радиусе Хилла, W2-C062).

Режим 'exact' (по умолчанию): каждое произведение округляется по IEEE 754 отдельно, а сумма —
math.fsum (точная сумма с одним округлением); результат одинаков на любой платформе.

Режим 'legacy-blas' — только для проверки run, созданных до W2-I035 (в summary.json нет
generator_numerics): повторяет прежние операции numpy/BLAS бит в бит; совпадение со стартами
такого run возможно лишь на процессоре с тем же ядром OpenBLAS, что при расчёте (W2-D036).
"""
from __future__ import annotations

import contextlib
import math

import numpy as np

NUMERICS_VERSION = 'exact-fsum-v1'
LEGACY = 'legacy-blas'
_mode = NUMERICS_VERSION


def mode() -> str:
    return _mode


@contextlib.contextmanager
def numerics(name):
    """Временно задать режим: NUMERICS_VERSION или LEGACY."""
    global _mode
    if name not in (NUMERICS_VERSION, LEGACY):
        raise ValueError(f'Неизвестный режим численной схемы {name!r}')
    previous, _mode = _mode, name
    try:
        yield name
    finally:
        _mode = previous


def dot(a, b) -> float:
    a, b = np.asarray(a, dtype=float).ravel(), np.asarray(b, dtype=float).ravel()
    if a.shape != b.shape:
        raise ValueError('Разная длина векторов')
    if _mode == LEGACY:
        return float(np.dot(a, b))
    return math.fsum(float(x)*float(y) for x, y in zip(a.tolist(), b.tolist()))


def norm(a) -> float:
    if _mode == LEGACY:
        return float(np.linalg.norm(np.asarray(a, dtype=float)))
    return math.sqrt(dot(a, a))


def matvec(m, v) -> np.ndarray:
    m, v = np.asarray(m, dtype=float), np.asarray(v, dtype=float)
    if m.ndim != 2 or v.ndim != 1 or m.shape[1] != v.shape[0]:
        raise ValueError('Несогласованные размеры матрицы и вектора')
    if _mode == LEGACY:
        return m @ v
    return np.array([dot(row, v) for row in m], dtype=float)


def matmul(a, b) -> np.ndarray:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 2 or b.ndim != 2 or a.shape[1] != b.shape[0]:
        raise ValueError('Несогласованные размеры матриц')
    if _mode == LEGACY:
        return a @ b
    return np.array([[dot(row, b[:, j]) for j in range(b.shape[1])] for row in a], dtype=float)
