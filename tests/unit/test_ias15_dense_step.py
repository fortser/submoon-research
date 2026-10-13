"""W2-I019: плотный вывод B (IAS15) в моментах сетки точен при пределе шага по местному
орбитальному времени (engine_compare.ias15_step_limit).

Без предела у himalia-outer шаг доходил до 0.08 P0 и состояние внутри шага ошибалось до 1.3e-5 a;
постоянный предел 0.025 P0 оставлял до 3.4e-6 у тесного перицентра (himalia-outer-0-0.0, r ≈ 0.36 a).
Меры — как в критерии τ. Эталон — шаги, оканчивающиеся точно в моментах сетки.
"""
import math
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
YEAR = 365.25*86400.0


@pytest.mark.parametrize('orbit, years', [('himalia-outer-90-0.0', 0.16), ('himalia-outer-0-0.0', 0.2)])
def test_ias15_samples_match_step_endpoints_on_small_host_outer_orbit(orbit, years):
    pytest.importorskip('rebound')
    pytest.importorskip('reboundx')
    from submoon_research.dynamics.engine_compare import NumericMode, ias15_step_limit, integrate_engine
    from submoon_research.dynamics.native_ias15 import NativeIAS15
    from submoon_research.workflows.w2_comparison import load_cases

    _, cases = load_cases(ROOT)
    case = next(c for c in cases if c['orbit_id'] == orbit)
    period, a = case['period_seconds'], case['a_km']
    na = 2*math.pi*a/period
    horizon = years*YEAR
    times = np.r_[0.0, np.geomspace(1e-3*horizon, horizon, 60)]
    mode = NumericMode()
    out = integrate_engine('ias15_reboundx', case['state'], case['gms'], case['figures'], case['radii'],
                           horizon, period=period, mode=mode, times=times)
    assert out['run_status'] == 'completed'
    sampled = {s['time']: np.asarray(s['state']).reshape(-1, 6)[-1] for s in out['samples']}
    native = NativeIAS15(np.asarray(case['state'], dtype=float), case['gms'], case['figures'],
                         epsilon=mode.epsilon, t0=0.0, initial_dt=min(period*1e-5, horizon))
    worst, segment = 0.0, None
    for t in times[1:]:
        while native.sim.t < t:
            segment = native.step(t, ias15_step_limit(native, period, case['gms'][0], mode.step_fraction))
        exact = np.asarray(segment(segment.right)).reshape(-1, 6)[-1]
        d = max(np.linalg.norm(sampled[t][:3]-exact[:3])/a, np.linalg.norm(sampled[t][3:]-exact[3:])/na)
        worst = max(worst, d)
    assert worst < 1e-7
