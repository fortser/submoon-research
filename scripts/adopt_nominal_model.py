"""Аудит первичных номиналов физической L1; неизменяемый отдельный продукт."""

import hashlib
import json
import math
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import spiceypy as spice

from submoon_research.execution import RunContext
from submoon_research.catalog.nominal_model import (
    BODY_IDS,
    pck_numbers,
    jovian_primary,
    pole_from_coefficients,
    quantity,
    validate_model,
)
from submoon_research.workflows.smoke import write_json, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/audits/W0_nominal_v2.yaml"))
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, "W0-nominal", wall_seconds=120, output_mib=32)
    with run:
        run.save_code(Path(__file__).resolve())
        config = run.ledger.yaml(args.config, registered=True)
        run.save_config(config)
        for path, digest in config["inputs_sha256"].items():
            run.ledger.bind(path, digest)
        run.ledger.verify_all()
        paths = config["sources"]
        gm = pck_numbers(run.ledger.text(paths["gm"]))
        sat = pck_numbers(run.ledger.text(paths["sat"]))
        pck = pck_numbers(run.ledger.text(paths["pck"]))
        jovian, jov = jovian_primary(run.ledger.text(paths["jov"]))
        bodies = {}
        checks = {}
        radii = {
            name: float(np.mean(pck[f"BODY{identifier}_RADII"]))
            for name, identifier in BODY_IDS.items()
            if name != "himalia"
        }
        radii["himalia"] = 85.0
        radii.update(iapetus=734.3, ganymede=2631.2)
        passports = {
            name: run.ledger.yaml(paths["passport_" + name])
            for name in ("iapetus", "ganymede", "himalia")
            if "passport_" + name in paths
        }
        for name, identifier in BODY_IDS.items():
            if name in ("jupiter", "io", "europa", "ganymede", "callisto"):
                value = jovian[identifier]
                source = "jup365_comments_20261004"
                locator = f"JUP365.26 body table {identifier}, both blocks"
            elif name in ("saturn", "titan", "iapetus"):
                value = sat[f"BODY{identifier}_GM"][0]
                source = "sat441_pck_20261004"
                locator = f"BODY{identifier}_GM"
            elif name == "himalia":
                value = gm[f"BODY{identifier}_GM"][0]
                source = "gm_de440_pck_20261005"
                locator = "BODY506_GM, JUP344 nominal preserved"
            else:
                value = gm["BODY10_GM"][0]
                source = "gm_de440_pck_20261005"
                locator = "BODY10_GM, physical Sun, not satellite effective GM10"
            q = quantity(
                value, "km3/s2", source, locator, definition="physical_body_center_monopole"
            )
            if name in ("saturn", "titan", "iapetus"):
                q.update(
                    uncertainty={"saturn": 0.726, "titan": 0.00074, "iapetus": 0.00727}[name],
                    uncertainty_sigma_level=3,
                    uncertainty_status="reported_formal_3sigma_no_joint_covariance",
                    uncertainty_source_id="jacobson2022_sat441",
                    uncertainty_source_locator="Table 4 p.6 Current and note",
                )
            checks[name + "_gm_kernel_crosscheck"] = bool(
                math.isclose(value, gm[f"BODY{identifier}_GM"][0], rel_tol=1e-15, abs_tol=0)
            )
            radius_source = (
                "archinal2018_wgccre"
                if name in ("himalia", "ganymede")
                else "thomas2010_shapes"
                if name == "iapetus"
                else "pck00011_20261005"
            )
            bodies[name] = dict(
                naif_id=identifier,
                gm=q,
                contact_radius=quantity(
                    radii[name],
                    "km",
                    radius_source,
                    "IAU reference radius"
                    if name in ("himalia", "ganymede")
                    else "Thomas2010 Table1"
                    if name == "iapetus"
                    else f"mean(BODY{identifier}_RADII)",
                    definition="operational_reference_sphere",
                ),
                physical_surface=None,
            )
            if name in passports:
                measured = passports[name]["parameters"]["radius"]
                checks[name + "_radius_matches_verified_passport"] = (
                    measured["value"] == radii[name] and measured["unit"] == "km"
                )
                radius = dict(measured)
                radius.update(
                    unit="km",
                    definition="operational_reference_sphere",
                    parent_radius_definition=measured.get("definition"),
                    status="adopted_nominal_for_conditional_L1",
                )
                bodies[name]["contact_radius"] = radius
        figures = {}
        orientation = []
        try:
            spice.kclear()
            spice.furnsh(str(run.folder / "inputs" / paths["pck"]))
            spice.furnsh(str(run.folder / "inputs" / paths["sat"]))
            # Полюс JUP365 переводится в формальные массивы SPICE из того же первичного блока.
            ra_amp = [jov[f"RA_AMPL{k}"] for k in range(1, 5)]
            dec_amp = [jov[f"DE_AMPL{k}"] for k in range(1, 5)]
            angles = [jov[f"RA_FAZE{k}"] for k in range(1, 5)]
            pairs = [x for k in range(1, 5) for x in (jov[f"RA_FAZE{k}"], jov[f"RA_FREQ{k}"])]
            spice.pdpool("BODY599_POLE_RA", [jov["ZACPL5"], jov["DACPL5"], 0.0])
            spice.pdpool("BODY599_POLE_DEC", [jov["ZDEPL5"], jov["DDEPL5"], 0.0])
            spice.pdpool("BODY599_NUT_PREC_RA", ra_amp)
            spice.pdpool("BODY599_NUT_PREC_DEC", dec_amp)
            spice.pdpool("BODY599_NUT_PREC_PM", [0.0] * 4)
            spice.pdpool("BODY5_NUT_PREC_ANGLES", pairs)
            for name, identifier in [("jupiter", 599), ("saturn", 699)]:
                pole = spice.pxform("IAU_" + name.upper(), "J2000", 0.0)[:, 2]
                if name == "jupiter":
                    expected = pole_from_coefficients(
                        jov["ZACPL5"], jov["ZDEPL5"], angles, ra_amp, dec_amp
                    )
                    j2, R, source = jov["J502"], jov["RADIUS"], "jup365_comments_20261004"
                else:
                    expected = pole_from_coefficients(
                        sat["BODY699_POLE_RA"][0],
                        sat["BODY699_POLE_DEC"][0],
                        sat["BODY6_NUT_PREC_ANGLES"][::2],
                        sat["BODY699_NUT_PREC_RA"],
                        sat["BODY699_NUT_PREC_DEC"],
                    )
                    j2, R, source = (
                        sat["BODY699_JCOEF"][1],
                        sat["BODY699_RADII"][0],
                        "sat441_pck_20261004",
                    )
                checks[name + "_independent_pole"] = bool(np.linalg.norm(pole - expected) < 1e-14)
                orientation.append(
                    dict(
                        body=name,
                        spice=pole.tolist(),
                        independent=expected.tolist(),
                        difference=float(np.linalg.norm(pole - expected)),
                    )
                )
                figures[name] = dict(
                    j2=quantity(
                        j2, "1", source, "J502" if name == "jupiter" else "BODY699_JCOEF[1]"
                    ),
                    reference_radius=quantity(
                        R,
                        "km",
                        source,
                        "RADIUS"
                        if name == "jupiter"
                        else "BODY699_RADII[0]; Jacobson2022 Table6 p.6 R=60330km",
                    ),
                    pole_icrf=quantity(
                        pole.tolist(),
                        "1",
                        source,
                        "JUP365 pole coefficients at T=0"
                        if name == "jupiter"
                        else "SAT441 pole polynomial and nutation at T=0",
                    ),
                    pole_epoch_jd_tdb=2451545.0,
                    pole_evolution="fixed_for_nominal_L1",
                    normalization="unnormalized_Legendre_P2",
                )
                if name == "saturn":
                    figures[name]["j2"].update(
                        uncertainty=0.025e-6,
                        uncertainty_sigma_level=3,
                        uncertainty_status="reported_formal_3sigma",
                        uncertainty_source_id="jacobson2022_sat441",
                        uncertainty_source_locator="Table6 p.6 Current and note",
                    )
        finally:
            spice.kclear()
        checks["planet_center_not_system_gm"] = bool(
            bodies["jupiter"]["gm"]["value"] < gm["BODY5_GM"][0]
            and bodies["saturn"]["gm"]["value"] < sat["BODY6_GM"][0]
        )
        checks["sun_not_effective_satellite_constant"] = bool(
            bodies["sun"]["gm"]["value"] != sat["BODY10_GM"][0]
        )
        model = dict(
            schema_version="0.4",
            model_id=config.get("model_id", "L1-nominal-v0.4"),
            supersedes=config.get("supersedes"),
            decision_id="W0-D011",
            production_allowed=False,
            baseline_integration_allowed=True,
            parameter_sampling_allowed=False,
            physical_surface_verified=False,
            host_figure=False,
            bodies=bodies,
            figures=figures,
            inputs_sha256=dict(run.ledger.used),
            source_paths=paths,
            units=dict(
                length="km", velocity="km/s", time="s", gm="km3/s2", epoch="JD TDB", frame="ICRF"
            ),
            year_days=365.25,
            day_seconds=86400,
            submoon_mass=0.0,
            submoon_radius_km=0.1,
            limitations=[
                "Truncated self-consistent selected N-body system, not ephemeris forcing",
                "Nominal operational spheres; no measured shape or parameter covariance ensemble",
                "Planet poles fixed at initial epoch; tides/radiation/host figure excluded",
            ],
        )
        validate_model(model)
        checks["complete_model_contract"] = True
        validation = run.validate(checks, scope="W0_nominal_parameter_audit_and_poles")
        write_json(run.folder / "results/model.json", model)
        write_json(run.folder / "results/pole_comparison.json", orientation)
        if validation["status"] == "passed" and args.publish:
            destination = root / config["output_model"]
            if destination.exists():
                raise FileExistsError("Не перезаписывать принятый каталог")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((run.folder / "results/model.json").read_bytes())
            write_json(
                root / config["output_manifest"],
                dict(
                    schema_version="0.1",
                    run_id=run.run_id,
                    files=[
                        dict(
                            path=destination.relative_to(root).as_posix(),
                            sha256=sha256(destination),
                        )
                    ]
                    + [dict(path=path, sha256=digest) for path, digest in run.ledger.used.items()],
                ),
            )
        write_json(
            run.folder / "results/adoption.json",
            dict(
                model_sha256=hashlib.sha256(
                    (run.folder / "results/model.json").read_bytes()
                ).hexdigest()
            ),
        )
    print(json.dumps(dict(run_id=run.run_id, status=validation["status"])))
    return int(validation["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
