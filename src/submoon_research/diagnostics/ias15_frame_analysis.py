"""Offline reference dense reconstruction; no additional integration or forces."""
from __future__ import annotations

import numpy as np

from submoon_research.contracts.p2_files import digest
from submoon_research.dynamics.dense_segments import PowerSegment
from submoon_research.dynamics.native_ias15 import NativeIAS15


def reference_samples(physical, replay, controller, grid):
    """Recreate only segments whose full coefficient SHA matches C073 exactly.

    The existing adapter's pure assembler is used without constructing a Simulation.
    End states, a0 and br are already saved; sampling uses the frozen common grid.
    """
    start = np.asarray(physical['absolute_state'], dtype=float)
    assembler = NativeIAS15.__new__(NativeIAS15)
    assembler.count = len(start)
    pending, verified = 1, 0
    samples = [dict(index=0, elapsed=grid['elapsed'][0], time=grid['absolute'][0],
                    relative_state=(start-start[:1]).tolist(), kind='initial')]
    if len(controller['records']) < len(replay['records']):
        raise ValueError('Incomplete reference buffers')
    for record, raw in zip(replay['records'], controller['records']):
        if any(record[k] != raw[k] for k in ('left', 'right', 'dt_done')):
            raise ValueError('Reference buffers are not from the same accepted step')
        coeff = assembler._assemble(start, np.asarray(raw['a0']), np.asarray(raw['br']), record['dt_done'])
        coeff -= coeff[:, :1, :]
        coeff = coeff.reshape(10, -1)
        if digest(coeff.tolist()) != record['coefficients_sha256']:
            raise ValueError('Reconstructed dense coefficient SHA mismatch')
        verified += 1
        segment = PowerSegment(record['left'], record['right'], coeff)
        while pending < len(grid['absolute']) and grid['absolute'][pending] <= segment.right:
            when = grid['absolute'][pending]
            if when < segment.left:
                raise ValueError('Common reference point lost')
            samples.append(dict(index=pending, elapsed=grid['elapsed'][pending], time=when,
                                relative_state=np.asarray(segment(when)).reshape(len(start), 6).tolist(),
                                kind='sha_verified_reconstructed_dense'))
            pending += 1
        start = np.asarray(record['absolute_state'])
    return dict(variant='N', samples=samples, verified_segments=verified,
                source='offline reconstruction; no new steps', scientific_admission=False)
