"""Прослеживаемые GM и разные ограничения размера Гималии."""
from __future__ import annotations

import math
import re

from submoon_research.workflows.baseline_audit import jpl_parameter_rows


def himalia_source_measurements(cmt, archinal, grav, porco, jpl):
    """Читать разные типы размеров без восстановления третьей оси."""
    gm_rows = re.findall(r"^\s*Himalia\s+506\s+(\S+)\s+2\s+8\s+SATORBINT\s*$", cmt, re.M)
    if len(gm_rows) != 1 or "Satellite Ephemeris: JUP344" not in cmt:
        raise ValueError("Неоднозначная или отсутствующая строка GM Himalia JUP344")
    parent_rows = re.findall(r"\b500GM\s+(\S+)", cmt)
    if len(set(parent_rows)) != 1:
        raise ValueError("Неоднозначное GM Юпитера в JUP344")
    gm, parent_gm = float(gm_rows[0]), float(parent_rows[0])
    if not all(math.isfinite(v) and v > 0 for v in (gm, parent_gm)):
        raise ValueError("GM должен быть положительным конечным числом")
    iau_row = next(line for line in archinal.splitlines() if re.match(r"\s*VI\s+Himalia\s", line))
    pair = re.search(r"Himalia\s+(\d+)\s*±\s*(\d+)", iau_row)
    if pair is None or "frequently have different meanings" not in archinal:
        raise ValueError("Не найдены сводочный радиус IAU или оговорка об ошибках")
    thermal_table = grav.split("Thermal Fit Results", 1)[1]
    thermal_row = next(line for line in thermal_table.splitlines() if re.match(r"\s*J6\s+Himalia\s", line))
    thermal = re.search(r"Himalia\s+(\d+\.\d+)\s*±\s*(\d+\.\d+)", thermal_row)
    if thermal is None or "effective diameter of 139.6" not in grav:
        raise ValueError("Не найден эффективный тепловой диаметр опубликованной статьи")
    # Текст авторского извлечения имеет переносы; проверяем измерение, не пересказ.
    cleaned = " ".join(porco.split())
    axes = re.search(r"radii of (\d+)\s*±\s*(\d+) km by (\d+)\s*±\s*(\d+) km", cleaned)
    if axes is None or "principal axes (or dimensions close to them)" not in cleaned:
        raise ValueError("Не найдены две видимые полуоси и ограничение Porco")
    values = [float(v) for v in axes.groups()]
    return {
        "gm_km3_s2": gm, "parent_gm_km3_s2": parent_gm,
        "iau_radius_km": float(pair[1]), "iau_radius_error_km": float(pair[2]),
        "thermal_diameter_km": float(thermal[1]), "thermal_diameter_error_km": float(thermal[2]),
        "visible_semiaxes_km": [values[0], values[2]],
        "visible_semiaxes_error_km": [values[1], values[3]],
        "jpl": jpl_parameter_rows(jpl, ["Himalia"])["Himalia"],
    }


def verify_himalia_passport(passport, source):
    """Проверить значения и границы их трактовки, без допуска динамики."""
    gm, radius = (passport["parameters"][name] for name in ("gm", "radius"))
    thermal = passport["size_constraints"]["thermal_effective_radius"]
    visible = passport["size_constraints"]["cassini_visible_semiaxes"]
    checks = {
        "identity": passport["host_id"] == "himalia" and passport["identifiers"]["naif"] == 506,
        "gm_primary_exact": gm["value"] == source["gm_km3_s2"],
        "gm_jpl_rounding": round(gm["value"], 5) == source["jpl"]["gm_km3_s2"]["value"],
        "gm_error_role_preserved": gm["uncertainty"] == source["jpl"]["gm_km3_s2"]["uncertainty"]
            and gm["uncertainty_source_id"] == "jpl_satellite_parameters_20261004"
            and gm["uncertainty_status"] == "reported_aggregator_sigma_unverified"
            and gm["uncertainty_sigma_level"] is None,
        "radius_compilation_exact": radius["value"] == source["iau_radius_km"]
            == source["jpl"]["radius_km"]["value"]
            and radius["uncertainty"] == source["iau_radius_error_km"],
        "radius_definition_not_promoted": radius["definition"] == passport["radius_definition"]
            == "iau_compiled_mean_radius" and radius["status"] == "compiled_reference_only",
        "thermal_diameter_halved": thermal["value"] == source["thermal_diameter_km"] / 2
            and thermal["uncertainty"] == source["thermal_diameter_error_km"] / 2
            and thermal["definition"] == "thermal_model_effective_diameter_divided_by_two",
        "visible_axes_primary": visible["value"] == source["visible_semiaxes_km"]
            and visible["uncertainty"] == source["visible_semiaxes_error_km"]
            and visible["definition"] == "two_visible_semiaxes_conditional_on_principal_axis_identification",
        "unknown_size_sigma_preserved": all(p["uncertainty_sigma_level"] is None
            and p["uncertainty_distribution"] is None for p in (radius, thermal, visible)),
        "units": gm["unit"] == gm["uncertainty_unit"] == "km3 / s2"
            and all(p["unit"] == p["uncertainty_unit"] == "km" for p in (radius, thermal, visible)),
        "source_roles": gm["source_id"] == "jup344_comments_20261004"
            and radius["source_id"] == "archinal2018_wgccre"
            and thermal["source_id"] == "grav2015_neowise"
            and visible["source_id"] == "porco2003_himalia_extract",
        "traceable_parameters": all(all(p.get(k) for k in
            ("source_id", "source_locator", "source_version", "uncertainty_status"))
            for p in (gm, radius, thermal, visible)),
        "unknown_volume_and_third_axis": passport["shape"]["axes"] is None
            and passport["shape"]["volume_equivalent_radius"] is None
            and passport["shape"]["contact_surface"] is None and passport["pole"] is None,
        "unknown_covariance_not_zero": passport["covariance"]["matrix"] is None
            and passport["covariance"]["independent_sampling_authorized"] is False
            and all(p["covariance_ref"] is None for p in (gm, radius, thermal, visible)),
        "unknown_state_not_fabricated": all(passport["state"][key] is None for key in
            ("epoch", "time_scale", "origin", "frame", "position", "velocity", "source_id")),
        "no_unverified_usage": all(passport["usage"][key] is False for key in
            ("production_integration", "uncertainty_ensemble", "full_W0_handoff", "physical_contact_model")),
    }
    return checks, {
        "relative_reported_gm_error": gm["uncertainty"] / gm["value"],
        "iau_minus_thermal_radius_km": radius["value"] - thermal["value"],
        "independence_or_probability_distribution_assumed": False,
    }


def himalia_archive_comparison(states, source, au_km, g_si):
    """Отношения масс обходят неустановленную архивную солнечную единицу."""
    host = next(row["values"] for row in states if row["name"] == "HIMALIA")
    parent = next(row["values"] for row in states if row["name"] == "JUPITER")
    archive_ratio = host[0] / parent[0]
    primary_ratio = source["gm_km3_s2"] / source["parent_gm_km3_s2"]
    factor = archive_ratio / primary_ratio
    return {
        "archive_host_mass_solar_unit": host[0], "archive_parent_mass_solar_unit": parent[0],
        "archive_radius_km": host[1] * au_km,
        "archive_host_to_parent_mass_ratio": archive_ratio,
        "primary_host_to_parent_gm_ratio": primary_ratio,
        "archive_to_primary_relative_mass_factor": factor,
        "hill_scale_factor_at_fixed_a_and_parent": factor ** (1 / 3),
        "nominal_gm_equivalent_mass_kg": source["gm_km3_s2"] * 1e9 / g_si,
        "equivalent_mass_uncertainty_kg": None,
        "equivalent_mass_status": "diagnostic_conversion_not_independent_measurement",
    }
