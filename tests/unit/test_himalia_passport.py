"""Проверка отказа при искажении данных и научной трактовки."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from submoon_research.catalog.himalia_passport import (
    himalia_source_measurements,
    verify_himalia_passport,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def readings():
    config = yaml.safe_load((ROOT / "configs/audits/himalia_passport_v1.yaml").read_text(encoding="utf-8"))
    return {k: (ROOT / p).read_text(encoding="utf-8") for k, p in config["source_paths"].items()}


def test_rejects_duplicate_primary_gm(readings):
    readings["cmt"] += "\nHimalia 506 1.5E-1 2 8 SATORBINT\n"
    with pytest.raises(ValueError, match="Неоднозначная"):
        himalia_source_measurements(**readings)


def test_rejects_nonfinite_gm(readings):
    readings["cmt"] = readings["cmt"].replace("1.515524299611265E-01", "NaN")
    with pytest.raises(ValueError, match="конечным"):
        himalia_source_measurements(**readings)


@pytest.mark.parametrize("mutation,check", [
    ("sigma", "gm_error_role_preserved"),
    ("volume", "radius_definition_not_promoted"),
    ("third_axis", "unknown_volume_and_third_axis"),
    ("unit", "units"),
    ("sampling", "unknown_covariance_not_zero"),
])
def test_rejects_unjustified_scientific_interpretation(readings, mutation, check):
    passport = deepcopy(yaml.safe_load((ROOT / "data/processed/host_passports/himalia_v1.yaml").read_text(encoding="utf-8")))
    if mutation == "sigma":
        passport["parameters"]["gm"]["uncertainty_sigma_level"] = 1
    elif mutation == "volume":
        passport["parameters"]["radius"]["definition"] = "volume_equivalent_mean"
    elif mutation == "third_axis":
        passport["shape"]["axes"] = [75, 60, 60]
    elif mutation == "unit":
        passport["parameters"]["gm"]["unit"] = "m3 / s2"
    elif mutation == "sampling":
        passport["covariance"]["independent_sampling_authorized"] = True
    checks, _ = verify_himalia_passport(passport, himalia_source_measurements(**readings))
    assert checks[check] is False
