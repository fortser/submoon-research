"""Проверка паспорта Япета по сохранённым первичным источникам."""
from __future__ import annotations

import math
import re

from submoon_research.workflows.baseline_audit import jpl_parameter_rows


def pck_values(text, key):
    """Читать числовую запись только из begindata; не выполнять код ядра."""
    data, enabled = [], False
    for line in text.splitlines():
        if line.strip() == r"\begindata":
            enabled = True
        elif line.strip() == r"\begintext":
            enabled = False
        elif enabled:
            data.append(line)
    matches = re.findall(r"\b" + re.escape(key) + r"\s*=\s*\(([^)]*)\)", "\n".join(data))
    if len(matches) != 1:
        raise ValueError(f"Требуется единственная запись PCK: {key}")
    tokens = re.split(r"[\s,]+", matches[0].strip())
    number = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?"
    if not tokens or any(not re.fullmatch(number, token) for token in tokens):
        raise ValueError(f"Некорректные числа PCK: {key}")
    values = [float(token.replace("D", "E").replace("d", "e")) for token in tokens]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Бесконечное значение PCK")
    return values


def iapetus_source_measurements(pck, thomas, jacobson, jpl):
    """Извлечь таблицы с сохранением разных уровней sigma."""
    table = thomas.split("Table 1", 1)[1].split("Table 2", 1)[0]
    row = next(line for line in table.splitlines() if re.match(r"\s*Iapetus\s", line))
    pairs = [(float(a), float(b)) for a, b in re.findall(r"(\d+\.\d+)\s*±\s*(\d+\.\d+)", row)]
    if len(pairs) < 4 or "two-sigma" not in table or "sphere of equivalent volume" not in table:
        raise ValueError("Не найдены радиусы, определение или уровень ошибки Thomas Table 1")
    row = next(line for line in jacobson.splitlines() if re.match(r"\s*GMIapetus\s", line))
    gm_pairs = re.findall(r"(\d+\.\d+)\s*±\s*(\d+\.\d+)", row)
    if "quoted uncertainties are 3σ" not in jacobson or len(gm_pairs) != 2:
        raise ValueError("Не найдено Current GM и пояснение 3sigma в Jacobson Table 4")
    physical = jacobson.split("Satellite Physical Properties", 1)[1].split("Table 6", 1)[0]
    row = next(line for line in physical.splitlines() if re.match(r"\s*Iapetus\s", line))
    pair = re.search(r"(\d+\.\d+)\s*±\s*(\d+\.\d+)", row)
    if pair is None or "Quoted uncertainties are 1σ" not in physical:
        raise ValueError("Не найден конфликт уровня ошибки Jacobson Table 5")
    return {
        "kernel_gm": pck_values(pck, "BODY608_GM")[0],
        "kernel_axes": pck_values(pck, "BODY608_RADII"),
        "paper_gm": float(gm_pairs[-1][0]),
        "paper_gm_uncertainty": float(gm_pairs[-1][1]),
        "axes": [pair[0] for pair in pairs[:3]],
        "axes_uncertainty": [pair[1] for pair in pairs[:3]],
        "radius": pairs[3][0], "radius_uncertainty": pairs[3][1],
        "radius_sigma_level_primary": 2,
        "radius_sigma_level_jacobson_table5": 1,
        "radius_jacobson_table5": [float(x) for x in pair.groups()],
        "jpl": jpl_parameter_rows(jpl, ["Iapetus"])["Iapetus"],
    }


def verify_iapetus_passport(passport, source, volume_tolerance_km=0.1):
    """Проверить номинальные параметры; допуск production не выдаётся."""
    gm, radius = passport["parameters"]["gm"], passport["parameters"]["radius"]
    axes = passport["shape"]["axes"]
    checks = {
        "identity": passport["host_id"] == "iapetus" and passport["identifiers"]["naif"] == 608,
        "gm_kernel_exact": gm["value"] == source["kernel_gm"],
        "gm_article_rounding": round(gm["value"], 5) == source["paper_gm"],
        "gm_uncertainty_primary": gm["uncertainty"] == source["paper_gm_uncertainty"]
            and gm["uncertainty_sigma_level"] == 3,
        "gm_unit": gm["unit"] == "km3 / s2" and gm["uncertainty_unit"] == gm["unit"],
        "radius_primary": radius["value"] == source["radius"]
            and radius["uncertainty"] == source["radius_uncertainty"]
            and radius["uncertainty_sigma_level"] == 2,
        "radius_definition": radius["definition"] == passport["radius_definition"]
            == "volume_equivalent_mean" and radius["unit"] == radius["uncertainty_unit"] == "km",
        "axes_primary_and_kernel": axes["value"] == source["axes"] == source["kernel_axes"]
            and axes["uncertainty"] == source["axes_uncertainty"]
            and axes["uncertainty_sigma_level"] == 2 and axes["unit"] == "km",
        "oblate_axes_dependency": axes["value"][0] == axes["value"][1]
            and passport["shape"]["joint_sampling"] == "disabled_pending_joint_model",
        "radius_jpl_nominal": radius["value"] == source["jpl"]["radius_km"]["value"],
        "gm_jpl_rounding": round(gm["value"], 5) == source["jpl"]["gm_km3_s2"]["value"]
            and round(gm["uncertainty"] / 3, 5) == source["jpl"]["gm_km3_s2"]["uncertainty"],
        "radius_conflict_preserved": source["radius_jacobson_table5"]
            == [radius["value"], radius["uncertainty"]]
            and source["radius_sigma_level_jacobson_table5"] != radius["uncertainty_sigma_level"]
            and bool(passport["open_questions"]),
        "unknown_covariance_not_zero": passport["covariance"]["matrix"] is None
            and passport["covariance"]["independent_sampling_authorized"] is False
            and all(p["covariance_ref"] is None for p in (gm, radius, axes)),
        "unknown_state_not_fabricated": all(passport["state"][key] is None for key in
            ("epoch", "time_scale", "origin", "frame", "position", "velocity", "source_id")),
        "no_unverified_usage": all(passport["usage"][key] is False for key in
            ("production_integration", "uncertainty_ensemble", "full_W0_handoff")),
        "traceable_parameters": all(all(p.get(key) for key in
            ("source_id", "source_locator", "source_version", "uncertainty_status"))
            for p in (gm, radius, axes)),
        "source_roles": gm["source_id"] == "sat441_pck_20261004"
            and gm["uncertainty_source_id"] == "jacobson2022_sat441"
            and radius["source_id"] == axes["source_id"] == "thomas2010_shapes",
    }
    volume_radius = math.prod(axes["value"]) ** (1 / 3)
    # Это допуск округления табличных размеров до 0.1 km, не физическая ошибка.
    checks["ellipsoid_volume_rounding"] = abs(volume_radius - radius["value"]) <= volume_tolerance_km
    return checks, {"ellipsoid_volume_radius_km": volume_radius,
                    "tabulated_radius_difference_km": volume_radius - radius["value"]}
