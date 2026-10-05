"""Отказы при подмене источников, физических ролей и эпохи архива."""
from copy import deepcopy
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from submoon_research.catalog.ganymede_passport import (
    audit_ganymede_archive,
    ganymede_source_measurements,
    verify_ganymede_passport,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def inputs():
    config = yaml.safe_load((ROOT / "configs/audits/ganymede_passport_v1.yaml").read_text(encoding="utf-8"))
    readings = {key: (ROOT / path).read_text(encoding="utf-8") for key, path in config["source_paths"].items()}
    passport = yaml.safe_load((ROOT / config["passport"]).read_text(encoding="utf-8"))
    return config, readings, passport


def test_segment_gm_conflict_rejected(inputs):
    _, readings, _ = inputs
    readings["cmt"] = readings["cmt"].replace("9.887832752719638E+03", "9.9E+03", 1)
    with pytest.raises(ValueError, match="Неоднозначная"):
        ganymede_source_measurements(**readings)


def test_nonfinite_primary_gm_rejected(inputs):
    _, readings, _ = inputs
    readings["cmt"] = readings["cmt"].replace("9.887832752719638E+03", "NaN")
    with pytest.raises(ValueError, match="конечные"):
        ganymede_source_measurements(**readings)


@pytest.mark.parametrize("mutation,check", [
    ("sigma", "unknown_sigma_and_distribution"),
    ("radius", "radius_role_preserved"),
    ("axes", "unknown_shape_not_fabricated"),
    ("gravity", "gravity_not_adopted"),
    ("sampling", "unknown_covariance_not_zero"),
    ("epoch", "unknown_state_not_fabricated"),
])
def test_unjustified_interpretations_rejected(inputs, mutation, check):
    _, readings, original = inputs
    passport = deepcopy(original)
    if mutation == "sigma":
        passport["parameters"]["gm"]["uncertainty_sigma_level"] = 1
    elif mutation == "radius":
        passport["parameters"]["radius"]["definition"] = "volume_equivalent_mean"
    elif mutation == "axes":
        passport["shape"]["axes"] = [2631.2] * 3
    elif mutation == "gravity":
        passport["gravity"]["j2"] = 1.335e-4
    elif mutation == "sampling":
        passport["covariance"]["independent_sampling_authorized"] = True
    elif mutation == "epoch":
        passport["state"]["time_scale"] = "TDB"
    checks = verify_ganymede_passport(passport, ganymede_source_measurements(**readings))
    assert checks[check] is False


def test_archive_detects_physical_differences_and_keeps_every_member(inputs):
    config, readings, _ = inputs
    with ZipFile(ROOT / config["baseline_archive"]) as archive:
        result = audit_ganymede_archive(archive, ganymede_source_measurements(**readings), config["au_km"])
    assert len(result["member_hashes"]) == 130
    assert len(result["modal_members"]) == 124
    assert len(result["nonmodal_members"]) == 6
    assert all(any(abs(item["delta_distance_km"]) > 1 for item in group["distance_invariants"])
               for group in result["differences"])
    assert result["epoch"] is None and result["time_scale"] is None and result["frame"] is None
