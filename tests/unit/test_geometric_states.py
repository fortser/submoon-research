"""Синтетические контрольные данные; не реальные состояния и не результаты W0."""

import copy
from pathlib import Path

import astropy.units as u
import numpy as np
import pytest
import yaml

from submoon_research.acquisition.geometric_states import (
    assemble_states,
    build_requests,
    parse_vectors,
)


@pytest.fixture
def config():
    return yaml.safe_load(Path("configs/acquisition/W0_geometric_states_v1.yaml").read_text(encoding="utf-8"))


def synthetic_response(config):
    request = build_requests(config)[0]["request"]
    rows = [dict(JDTDB="2451545.0", X="3", Y="4", Z="0", VX="1", VY="2", VZ="3", RG="5"),
            dict(JDTDB="2451545.000694444", X="3", Y="4", Z="0", VX="1", VY="2", VZ="3", RG="5")]
    before = """Target body name: Sun (10) {source: SYNTHETIC_TEST_ONLY}
Center body name: Solar System Barycenter (0) {source: SYNTHETIC_TEST_ONLY}
Start time: A.D. 2000-Jan-01 12:00:00.0000 TDB
Stop  time: A.D. 2000-Jan-01 12:01:00.0000 TDB
Output units: AU-D
Output type: GEOMETRIC cartesian states
Reference frame: ICRF
JDTDB, X, Y, Z, VX, VY, VZ, RG
"""
    text = before + "$$SOE\n" + "\n".join(
        ",".join(row[key] for key in rows[0]) for row in rows) + "\n$$EOE\n"
    params = request["params"]
    response = dict(status="success", cache_hit=False, truncated=False, limit_reached=False,
        row_count=2, request=request, rows=rows, metadata=dict(horizons_parameters={
            "COMMAND": "10", "CENTER": "500@0", "START_TIME": params["start"],
            "STOP_TIME": params["stop"], "STEP_SIZE": "1m", "EPHEM_TYPE": "VECTORS",
            "TIME_TYPE": "TDB", "REF_SYSTEM": "ICRF", "REF_PLANE": "FRAME", "VEC_CORR": "NONE",
            "OUT_UNITS": "AU-D", "CSV_FORMAT": "YES"}))
    return text, response, request


def test_conversion_against_astropy_and_exact_physical_units(config):
    text, response, request = synthetic_response(config)
    result = parse_vectors(text, response, request, config)
    state = result["states_km_km_s"][0]
    assert state[:3] == pytest.approx((np.array([3, 4, 0]) * u.au).to_value(u.km), rel=1e-15)
    assert state[3:] == pytest.approx((np.array([1, 2, 3]) * u.au / u.day).to_value(u.km / u.s), rel=1e-15)


@pytest.mark.parametrize("old,new", [
    ("AU-D", "KM-S"), ("ICRF", "Ecliptic J2000"), ("GEOMETRIC", "ASTROMETRIC"),
    ("(10)", "(599)"), ("(0)", "(5)"), ("TDB", "UT"),
    ("2451545.0,", "2451544.0,"), (",3,4,0,", ",nan,4,0,"),
])
def test_reject_wrong_physical_contract_in_raw_header_or_rows(config, old, new):
    text, response, request = synthetic_response(config)
    with pytest.raises(ValueError):
        parse_vectors(text.replace(old, new), response, request, config)


def test_reject_light_time_corrected_request_even_with_geometric_header(config):
    text, response, request = synthetic_response(config)
    response["metadata"]["horizons_parameters"]["VEC_CORR"] = "LT"
    with pytest.raises(ValueError):
        parse_vectors(text, response, request, config)


def test_detect_disagreement_between_bridge_table_and_raw_text(config):
    text, response, request = synthetic_response(config)
    response["rows"][0]["VX"] = "-1"
    with pytest.raises(ValueError):
        parse_vectors(text, response, request, config)


def test_change_of_origin_invariant_under_common_translation_and_velocity(config):
    # Независимые простые численные ответы; один и тот же сдвиг r и v всех тел.
    parsed = {name: dict(epoch_jd_tdb=[2451545., 2451545.000694444],
        states_km_km_s=[[index * 3., index * 2., index * 7., index * .1, index * .2, index * .3]] * 2,
        source={}) for index, name in enumerate(config["targets"])}
    for host, parent in config["parents"].items():
        state = np.array(parsed[parent]["states_km_km_s"]) - np.array(parsed[host]["states_km_km_s"])
        parsed[f"direct_{host}"] = dict(epoch_jd_tdb=parsed[host]["epoch_jd_tdb"], states_km_km_s=state.tolist(), source={})
    states, checks, _ = assemble_states(parsed, config)
    assert all(checks.values())
    shifted = copy.deepcopy(parsed)
    for name in config["targets"]:
        shifted[name]["states_km_km_s"] = (np.array(parsed[name]["states_km_km_s"]) + [100, -100, 100, 10, -10, 10]).tolist()
    translated, checks, _ = assemble_states(shifted, config)
    assert all(checks.values())
    for host, bodies in states["host_relative"].items():
        for name, state in bodies.items():
            assert np.allclose(state, translated["host_relative"][host][name], atol=1e-14, rtol=0)
    shifted["direct_iapetus"]["states_km_km_s"][0][0] += 0.002
    _, checks, _ = assemble_states(shifted, config)
    assert checks["iapetus_direct_position"] is False
    assert checks["iapetus_direct_velocity"] is True
