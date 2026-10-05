"""Принятые номиналы физических центров; неизвестные ошибки не выдумываются."""

import re
import numpy as np


BODY_IDS = dict(
    sun=10,
    jupiter=599,
    saturn=699,
    titan=606,
    io=501,
    europa=502,
    ganymede=503,
    callisto=504,
    iapetus=608,
    himalia=506,
)


def pck_numbers(text):
    blocks = re.findall(r"\\begindata(.*?)(?=\\begintext|\Z)", text, re.S)
    result = {}
    for block in blocks:
        for key, raw in re.findall(r"(BODY\d+_[A-Z0-9_]+)\s*=\s*\(([^)]*)\)", block):
            if "'" in raw:
                continue
            values = [
                float(token.replace("D", "E").replace("d", "e"))
                for token in raw.replace(",", " ").split()
            ]
            if not values or not np.isfinite(values).all() or key in result:
                raise ValueError("Неоднозначная численная запись PCK: " + key)
            result[key] = values
    return result


def jovian_primary(text):
    blocks = re.split(r"^Satellite Ephemeris:\s*JUP365\.26\s*$", text, flags=re.M)[1:]
    if len(blocks) != 2:
        raise ValueError("Ожидаются два первичных блока JUP365.26")
    parsed = []
    for block in blocks:
        table, constants = block.split("Additional Constants on the File:", 1)
        constants = constants.split("Planet gravitational harmonics", 1)[0]
        if "Planet gravitational harmonics are unnormalized" not in block:
            raise ValueError("Нет первичного определения нормировки")
        bodies = {
            int(i): float(g)
            for i, g in re.findall(
                r"^\s*\w+\s+(\d+)\s+(\S+)\s+\d+\s+\d+\s+SATORBINT\s*$", table, re.M
            )
        }
        numbers = {
            k: float(v)
            for k, v in re.findall(r"\b([A-Z0-9_]+)\s+([-+]?\d\.\d+E[-+]\d+)", constants)
        }
        parsed.append((bodies, numbers))
    if parsed[0] != parsed[1]:
        raise ValueError("Прямой и обратный JUP365 имеют разные параметры")
    return parsed[0]


def pole_from_coefficients(ra, dec, angles, ra_amplitudes, dec_amplitudes):
    """IAU/SPICE полюс при T=0: RA sin(theta), DEC cos(theta)."""
    angles = np.deg2rad(np.asarray(angles, dtype=float))
    right_ascension = np.deg2rad(ra + np.dot(ra_amplitudes, np.sin(angles[: len(ra_amplitudes)])))
    declination = np.deg2rad(dec + np.dot(dec_amplitudes, np.cos(angles[: len(dec_amplitudes)])))
    return np.array(
        [
            np.cos(declination) * np.cos(right_ascension),
            np.cos(declination) * np.sin(right_ascension),
            np.sin(declination),
        ]
    )


def quantity(
    value,
    unit,
    source,
    locator,
    *,
    uncertainty=None,
    uncertainty_status="not_provided_in_primary_constant_file",
    sigma_level=None,
    definition=None,
):
    return dict(
        value=value,
        unit=unit,
        source_id=source,
        source_locator=locator,
        source_version=source,
        uncertainty=uncertainty,
        uncertainty_status=uncertainty_status,
        uncertainty_sigma_level=sigma_level,
        covariance_ref=None,
        covariance_status="not_acquired",
        definition=definition,
        status="adopted_nominal_for_conditional_L1",
    )


def validate_model(model):
    if set(model) != {
        "schema_version",
        "model_id",
        "supersedes",
        "decision_id",
        "production_allowed",
        "baseline_integration_allowed",
        "parameter_sampling_allowed",
        "physical_surface_verified",
        "host_figure",
        "bodies",
        "figures",
        "inputs_sha256",
        "source_paths",
        "units",
        "year_days",
        "day_seconds",
        "submoon_mass",
        "submoon_radius_km",
        "limitations",
    }:
        raise ValueError("Неизвестные или отсутствующие поля номинальной модели")
    if (
        model["baseline_integration_allowed"] is not True
        or model["decision_id"] != "W0-D011"
        or model["submoon_mass"] != 0.0
        or model["submoon_radius_km"] != 0.1
        or model["day_seconds"] != 86400
        or model["year_days"] != 365.25
        or model["units"]
        != dict(length="km", velocity="km/s", time="s", gm="km3/s2", epoch="JD TDB", frame="ICRF")
        or set(model["figures"]) != {"jupiter", "saturn"}
    ):
        raise ValueError("Не принята научная постановка номинальной модели")
    if (
        model["schema_version"] != "0.4"
        or model["production_allowed"] is not False
        or model["host_figure"] is not False
    ):
        raise ValueError("Неверный номинальный уровень модели")
    if set(model["bodies"]) != set(BODY_IDS):
        raise ValueError("Неполный набор номиналов")
    for name, body in model["bodies"].items():
        gm = body["gm"]
        if (
            set(body) != {"naif_id", "gm", "contact_radius", "physical_surface"}
            or body["physical_surface"] is not None
            or not np.isfinite(gm["value"])
            or gm["definition"] != "physical_body_center_monopole"
            or not gm["source_version"]
            or not gm["uncertainty_status"]
            or body["naif_id"] != BODY_IDS[name]
            or gm["unit"] != "km3/s2"
            or not gm["value"] > 0
            or not gm["source_id"]
        ):
            raise ValueError("Неверный GM физического центра " + name)
        radius = body["contact_radius"]
        if (
            radius["unit"] != "km"
            or not np.isfinite(radius["value"])
            or radius["value"] <= 0
            or radius["definition"] != "operational_reference_sphere"
        ):
            raise ValueError("Не определена контактная сфера " + name)
    for name, figure in model["figures"].items():
        if (
            name not in ("jupiter", "saturn")
            or figure["normalization"] != "unnormalized_Legendre_P2"
        ):
            raise ValueError("Не определена нормировка поля")
        if (
            figure["j2"]["unit"] != "1"
            or not np.isfinite(figure["j2"]["value"])
            or not 0 < figure["j2"]["value"] < 0.1
            or figure["reference_radius"]["unit"] != "km"
            or not np.isfinite(figure["reference_radius"]["value"])
            or figure["reference_radius"]["value"] <= 0
        ):
            raise ValueError("Неверное поле J2")
        if (
            np.asarray(figure["pole_icrf"]["value"]).shape != (3,)
            or not np.isfinite(figure["pole_icrf"]["value"]).all()
            or abs(np.linalg.norm(figure["pole_icrf"]["value"]) - 1) > 1e-12
            or figure["pole_epoch_jd_tdb"] != 2451545.0
            or figure["pole_evolution"] != "fixed_for_nominal_L1"
        ):
            raise ValueError("Неверный полюс")
    if (
        not model["inputs_sha256"]
        or model["parameter_sampling_allowed"]
        or model["physical_surface_verified"]
    ):
        raise ValueError("Недопустимое обобщение номинальной модели")
    return model
