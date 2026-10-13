"""W2-I021: последний шаг B не выходит за горизонт (метка REBOUND t = fl(t + dt) могла превысить
его на несколько ulp; проверка найдена приёмщиком P2-2 на единице iapetus:uniform_a_e_cos_i_phases:0000)."""
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip('rebound')
pytest.importorskip('reboundx')

ROOT = Path(__file__).resolve().parents[2]
YEAR = 365.25*86400.0


def test_b_ends_exactly_at_horizon():
    sys.path.insert(0, str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location('admission_p2_2', ROOT/'scripts'/'admission_p2_2.py')
    stand = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stand)
    from submoon_research.dynamics.engine_compare import NumericMode, integrate_engine
    for horizon in (0.0003*YEAR, 0.001*YEAR):
        for unit in stand.units(1):
            out = integrate_engine('ias15_reboundx', unit['state'], unit['gms'], unit['figures'], unit['radii'],
                                   horizon, period=unit['period_seconds'], mode=NumericMode(),
                                   hill_km=unit['hill_km'], escape_window=unit['escape_window'],
                                   times=stand.grid(horizon))
            assert out['run_status'] == 'completed', out['error']
            assert out['last_valid_time'] <= horizon
            if out['event'] is None:
                assert out['last_valid_time'] == horizon
                assert out['samples'][-1]['time'] == horizon
                assert out['checkpoint']['time'] == horizon
