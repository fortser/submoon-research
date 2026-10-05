import copy
import hashlib
import json
import numpy as np
import pytest

from submoon_research.catalog.nominal_model import BODY_IDS, quantity, validate_model
from submoon_research.contracts.nominal_states import HOST_SETS, validate_nominal_states
from submoon_research.contracts.checkpoint import validate_checkpoint
from submoon_research.contracts.baseline import BaselineConfig, validate_prerequisite


def test_baseline_cannot_bypass_numeric_gates_with_empty_or_fake_passed():
    import yaml
    from pathlib import Path

    config = yaml.safe_load(
        (
            Path(__file__).resolve().parents[2] / "configs/experiments/W0_nominal_baseline_v1.yaml"
        ).read_text(encoding="utf-8")
    )
    BaselineConfig.model_validate(config)
    for changed in [config | dict(prerequisites={}), config | dict(inputs_sha256={})]:
        with pytest.raises(ValueError):
            BaselineConfig.model_validate(changed)
    for name in ["V02", "V03_V04"]:
        with pytest.raises(ValueError):
            validate_prerequisite(name, dict(status="passed", checks={}))


def model_fixture():
    model = dict(
        schema_version="0.4",
        model_id="test",
        supersedes=None,
        decision_id="W0-D011",
        production_allowed=False,
        baseline_integration_allowed=True,
        parameter_sampling_allowed=False,
        physical_surface_verified=False,
        host_figure=False,
        bodies={},
        figures={},
        inputs_sha256={"a": "f" * 64},
        source_paths={},
        units=dict(
            length="km", velocity="km/s", time="s", gm="km3/s2", epoch="JD TDB", frame="ICRF"
        ),
        year_days=365.25,
        day_seconds=86400,
        submoon_mass=0.0,
        submoon_radius_km=0.1,
        limitations=[],
    )
    for name, code in BODY_IDS.items():
        model["bodies"][name] = dict(
            naif_id=code,
            physical_surface=None,
            gm=quantity(
                1.0, "km3/s2", "primary", "table", definition="physical_body_center_monopole"
            ),
            contact_radius=quantity(
                0.1, "km", "primary", "table", definition="operational_reference_sphere"
            ),
        )
    for name in ("jupiter", "saturn"):
        model["figures"][name] = dict(
            j2=quantity(0.01, "1", "primary", "table"),
            reference_radius=quantity(1.0, "km", "primary", "table"),
            pole_icrf=quantity([0.0, 0.0, 1.0], "1", "primary", "table"),
            normalization="unnormalized_Legendre_P2",
            pole_epoch_jd_tdb=2451545.0,
            pole_evolution="fixed_for_nominal_L1",
        )
    return model


@pytest.mark.parametrize(
    "mutation",
    [
        lambda m: m.update(tides=True),
        lambda m: m.update(submoon_mass=1.0),
        lambda m: m["units"].update(length="AU"),
        lambda m: m["bodies"]["sun"]["gm"].update(value=float("nan")),
        lambda m: m["bodies"]["sun"]["gm"].update(definition="system_effective"),
        lambda m: m["figures"]["saturn"]["reference_radius"].update(value=-1.0),
        lambda m: m["figures"]["saturn"].update(pole_evolution="precessing"),
        lambda m: m["figures"]["saturn"]["pole_icrf"].update(value=[float("nan"), 0, 1]),
    ],
)
def test_nominal_model_rejects_unversioned_scientific_mutations(mutation):
    model = model_fixture()
    validate_model(model)
    mutation(model)
    with pytest.raises(ValueError):
        validate_model(model)


def states_fixture():
    barycentric = {name: (np.ones((2, 6)) * index).tolist() for index, name in enumerate(BODY_IDS)}
    return dict(
        schema_version="0.1",
        data_kind="real_geometric_ephemeris_snapshot",
        synthetic=False,
        production_allowed=False,
        initial_epoch_index=0,
        epoch_jd_tdb=[2451545.0, 2451545.0 + 60 / 86400],
        time_scale="TDB",
        frame="ICRF",
        axes_motion="fixed",
        position_unit="km",
        velocity_unit="km/s",
        geometric_corrections="NONE",
        barycentric_origin="solar_system_barycenter",
        uncertainty=None,
        covariance_ref=None,
        barycentric=barycentric,
        sources={name: dict(target=dict(id=str(code))) for name, code in BODY_IDS.items()},
        host_relative={
            host: {
                name: (np.array(barycentric[name]) - np.array(barycentric[host])).tolist()
                for name in names
            }
            for host, names in HOST_SETS.items()
        },
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(time_scale="UTC"),
        lambda s: s.update(position_unit="AU"),
        lambda s: s.update(initial_epoch_index=True),
        lambda s: s["barycentric"]["sun"][0].__setitem__(0, float("inf")),
        lambda s: s["host_relative"]["iapetus"]["sun"][0].__setitem__(0, 123.0),
        lambda s: s["sources"]["jupiter"]["target"].update(id="5"),
        lambda s: s["epoch_jd_tdb"].__setitem__(0, 2451546.0),
    ],
)
def test_nominal_geometry_rejects_semantic_changes(mutation):
    states = states_fixture()
    validate_nominal_states(states)
    mutation(states)
    with pytest.raises(ValueError):
        validate_nominal_states(states)


@pytest.mark.parametrize(
    "field,value",
    [
        ("input_state_sha256", "x"),
        ("model_sha256", "x"),
        ("orbit_id", "another"),
        ("gms", [2.0]),
        ("rtol", 1e-3),
        ("horizon_seconds", 3.0),
        ("time", float("nan")),
        ("state", [float("nan")] * 12),
    ],
)
def test_checkpoint_rejects_wrong_science_numerics_or_state(field, value):
    initial = [0.0] * 12
    options = dict(rtol=1e-12, atol_position=1e-14, atol_velocity=1e-14, max_step=0.1)
    expected = dict(
        initial=initial,
        orbit_id="a",
        model_sha256="f" * 64,
        gms=[1.0],
        horizon=2.0,
        options=options,
    )
    cp = dict(
        schema_version="0.1",
        continuation="restart_from_exact_state_new_adaptive_history",
        time=1.0,
        state=initial,
        horizon_seconds=2.0,
        gms=[1.0],
        orbit_id="a",
        model_sha256="f" * 64,
        input_state_sha256=hashlib.sha256(json.dumps(initial).encode()).hexdigest(),
        **options,
    )
    validate_checkpoint(cp, **expected)
    damaged = copy.deepcopy(cp)
    damaged[field] = value
    with pytest.raises(ValueError):
        validate_checkpoint(damaged, **expected)
