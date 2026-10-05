import math
import json
from zipfile import ZipFile

import pytest

from submoon_research.workflows.baseline_audit import (
    archive_audit,
    jpl_parameter_rows,
    parse_initial_condition,
    sphere_mass,
    tex_number,
)


def test_explicit_tex_mass_and_unit_scaling():
    assert tex_number(r"5.55$\times10^{21 }$") == 5.55e21
    assert tex_number("133") == 133
    assert sphere_mass(100, 1000) == pytest.approx(4.18879020478639e9)
    # 0.1 km and 1 g/cm^3 expressed in SI represent the same physical body.
    assert sphere_mass(0.1 * 1000, 1 * 1000) == sphere_mass(100, 1000)


@pytest.mark.parametrize("value", [r"\mass", "nan", "5e21", "1+2"])
def test_unsupported_source_notation_is_not_invented(value):
    with pytest.raises(ValueError):
        tex_number(value)


@pytest.mark.parametrize("radius,density", [(0, 1000), (-100, 1000), (100, math.nan)])
def test_invalid_physical_assumptions_rejected(radius, density):
    with pytest.raises(ValueError):
        sphere_mass(radius, density)


def test_jpl_missing_uncertainty_does_not_become_zero():
    with pytest.raises(ValueError):
        jpl_parameter_rows("<tr><td>Hyperion</td><td>0.37</td><td>135 ±4</td></tr>",
                           ["Hyperion"])


def test_source_input_missing_units_is_rejected():
    with pytest.raises(ValueError, match="units"):
        parse_initial_condition("Name,Mass,Radius,X,Y,Z,VX,VY,VZ\nX,1,1,0,0,0,0,0,0")


def test_source_input_nonfinite_state_is_rejected():
    text = ("The units used are AU, Sun Mass, Day and rad\n"
            "Name,Mass,Radius,X,Y,Z,VX,VY,VZ\nX,1,1,nan,0,0,0,0,0\n#\n"
            "Body,J2,x_rot,y_rot,z_rot\nX,0.1,0,0,1\n#\n")
    with pytest.raises(ValueError, match="Non-finite"):
        parse_initial_condition(text)


def test_archive_numeric_diagnostics_remain_json_and_failed_checks_are_preserved(tmp_path):
    archive_path = tmp_path / "input.zip"
    text = ("The units used are AU, Sun Mass, Day and rad\n"
            "Name,Mass,Radius,X,Y,Z,VX,VY,VZ\n"
            "IAPETUS,1e-8,1e-6,0,0,0,0,0,0\n"
            "SATURN,1e-3,1e-4,1,0,0,0,0.001,0\n"
            "Sat_IAPETUS_1,1e-21,1e-10,0.001,0,0,0,0.001,0\n#\n"
            "Body,J2,x_rot,y_rot,z_rot\nSATURN,0.016291,0,0,1\n#\n")
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("README.txt", "Synthetic parser fixture; not physical data.")
        archive.writestr("Saturn/IAPETUS/sim_1.txt", text)
    result = archive_audit(archive_path, ["IAPETUS"])
    json.dumps(result, allow_nan=False)
    assert result["checks"]["each_host_has_130_unique_indices"] is False
    assert result["checks"]["normalized_planet_axes"] is True
