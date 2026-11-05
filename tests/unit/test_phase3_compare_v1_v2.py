"""Контроль ретроспективного разбора без новых интеграций."""
import json
from pathlib import Path

import pytest

from submoon_research.diagnostics.phase3_v2_input import load
from test_p2_file_contracts import load as load_script

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / 'results/evidence/W2_phase3_audit_20261030/v1/audit.json'
SHA = '0febe080e713cca4ea7cd6530beb46b7a89aa3f3fcff9b6bb17d69769e236150'


def test_retro_compare_complete_and_historical_v1():
    if not BASE.exists():
        pytest.skip('Аудированный срез отсутствует; восстановите его для реального сравнения')
    script = load_script('compare_phase3_v1_v2')
    data = load(ROOT, BASE, SHA)
    old = json.loads((ROOT / 'results/evidence/W2_phase3_analysis_20261028_pi/analysis.json').read_text(encoding='utf-8'))
    result = script.analyze(data, old)
    assert len(result['cases']) == 16
    assert result['transitions'] == {'K0->K0': 7, 'K1->K1': 1, 'K3->K3': 3, 'K3->K4': 3, 'K4->K4': 2}
    assert all(r['role'] in ('target', 'control') for r in result['cases'])
    c = next(r for r in result['cases'] if r['orbit_id'] == 'iapetus:canonical_volume:0006')
    assert c['v2_class'] == 'K4' and c['perturbation_grid']['unknown'] == 2
    assert c['physical_sensitivity']['1e-12']['physical_flips'] == 0
    with pytest.raises(ValueError, match='Классификация v1'):
        old['cases'][0]['classification']['cls'] = 'INVALID'
        script.analyze(data, old)
