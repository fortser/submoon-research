"""W2-T019: автоматическое решение по базовому прогону P2-2a (чистый расчёт, без стенда)."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    sys.path.insert(0, str(ROOT/'scripts'))
    spec = importlib.util.spec_from_file_location('baseline_gate_p2_2', ROOT/'scripts'/'baseline_gate_p2_2.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Модель плана §5 для полного P2-2a (порядок величин как в plan_model при c=0.5), секунды.
VOLUMES = dict(D=51.0*3600, T=101.6*3600, B=134.5*3600)
LONGEST = dict(D=0.54*3600, T=1.07*3600, B=9.01*3600)


def cost(d, t, b, jobs=50, model_hours=0.1):
    return {v: dict(jobs=jobs, model_core_hours=model_hours, measured_over_model=r)
            for v, r in dict(D=d, T=t, B=b).items()}


def test_go_with_b_warning():
    g = load()
    r = g.predict(cost(1.6, 1.6, 1.93), VOLUMES, LONGEST, 32, 19.5, 1.15)
    assert r['verdict'] == 'GO' and not r['reasons']
    p = r['projected_hours']
    assert p['volume'] == pytest.approx((51.0*1.6+101.6*1.6+134.5*1.93)/32)
    assert p['path_b'] == pytest.approx(9.01*1.93)
    assert p['need_dt'] == pytest.approx(max(p['volume'], p['path_dt'])*1.15)
    assert len(r['warnings']) == 1 and 'W2-D026' in r['warnings'][0]  # B 17.4×1.15 > 19.5


def test_failed_when_volume_does_not_fit():
    g = load()
    r = g.predict(cost(2.33, 2.33, 2.42), VOLUMES, LONGEST, 32, 19.5, 1.15)
    assert r['verdict'] == 'FAILED' and 'D/T не укладываются' in r['reasons'][0]
    # Те же скорости на 96 процессах укладываются.
    assert g.predict(cost(2.33, 2.33, 2.42), VOLUMES, LONGEST, 96, 19.5, 1.15)['verdict'] == 'GO'


def test_fast_machine_without_warnings():
    g = load()
    r = g.predict(cost(1.0, 1.0, 1.45), VOLUMES, LONGEST, 96, 20.0, 1.15)
    assert r['verdict'] == 'GO' and r['warnings'] == []
    assert r['i9_equivalent_speed']['B'] == pytest.approx(1/1.45)


def test_insufficient_or_invalid_baseline_fails():
    g = load()
    few = cost(1.0, 1.0, 1.45)
    few['B']['jobs'] = 3
    r = g.predict(few, VOLUMES, LONGEST, 32, 20.0, 1.15)
    assert r['verdict'] == 'FAILED' and any('мало данных' in x for x in r['reasons'])
    missing = cost(1.0, 1.0, 1.45)
    del missing['T']
    assert g.predict(missing, VOLUMES, LONGEST, 32, 20.0, 1.15)['verdict'] == 'FAILED'
    bad = cost(1.0, 1.0, float('nan'))
    assert g.predict(bad, VOLUMES, LONGEST, 32, 20.0, 1.15)['verdict'] == 'FAILED'
    assert g.predict(cost(1.0, 1.0, 1.45), VOLUMES, LONGEST, 0, 20.0, 1.15)['verdict'] == 'FAILED'
