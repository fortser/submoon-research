"""Паспорт Ганимеда: номинал JUP365, роли радиуса и аудит разных стартов."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict

from submoon_research.workflows.baseline_audit import jpl_parameter_rows, parse_initial_condition


def _consistent_positive(tokens, label):
    if not tokens:
        raise ValueError(f"Отсутствует {label}")
    values = [float(token) for token in tokens]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError(f"{label}: требуются положительные конечные числа")
    if len(set(values)) != 1:
        raise ValueError(f"Неоднозначная запись {label}")
    return values[0]


def ganymede_source_measurements(cmt, archinal, jpl, dlr, schenk):
    """Не приписывать сводке IAU первичную форму или вероятностную ошибку."""
    if cmt.count("Satellite Ephemeris: JUP365.26") != 2:
        raise ValueError("Не найдены две части JUP365.26")
    gm = _consistent_positive(re.findall(
        r"^\s*Ganymede\s+503\s+(\S+)\s+16\s+9\s+SATORBINT\s*$", cmt, re.M), "GM Ганимеда")
    parent = _consistent_positive(re.findall(
        r"^\s*Jupiter\s+599\s+(\S+)\s+8\s+15\s+SATORBINT\s*$", cmt, re.M), "GM Юпитера")
    size_table = archinal.split("Table 5 Size and shape parameters of the satellites", 1)[1]
    row = next(line for line in size_table.splitlines() if re.match(r"\s*III\s+Ganymede\s", line))
    radius = re.search(r"Ganymede\s+(\d+\.\d+)\s*±\s*(\d+\.\d+)\s+Same\s+Same\s+Same", row)
    if radius is None or "frequently have different meanings" not in archinal:
        raise ValueError("Не найдена строка IAU и оговорка об ошибках")
    if "Dieses Archiv kann nicht den Volltext" not in dlr:
        raise ValueError("Не подтверждено отсутствие полного текста Zubarev в DLR")
    if "preliminary" not in schenk or "500 m deviation" not in schenk:
        raise ValueError("Не найдены ограничения предварительного источника формы 2025")
    constants = {key: _consistent_positive(re.findall(r"\b" + key + r"\s+(\S+)", cmt), key)
                 for key in ("S3RADEQ", "S3J2", "S3C22")}
    return dict(gm_km3_s2=gm, parent_gm_km3_s2=parent,
                iau_radius_km=float(radius[1]), iau_radius_error_km=float(radius[2]),
                ephemeris_field_constants=constants,
                jpl=jpl_parameter_rows(jpl, ["Ganymede"])["Ganymede"])


def verify_ganymede_passport(passport, source):
    """Область приёмки ограничена номиналом GM и явно сводочным размером."""
    gm, radius = (passport["parameters"][key] for key in ("gm", "radius"))
    reference = passport["ephemeris_field_reference"]
    return {
        "identity": passport["host_id"] == "ganymede" and passport["identifiers"]["naif"] == 503,
        "gm_primary_exact": gm["value"] == source["gm_km3_s2"],
        "gm_jpl_rounding": round(gm["value"], 5) == source["jpl"]["gm_km3_s2"]["value"],
        "gm_error_role_preserved": gm["uncertainty"] == source["jpl"]["gm_km3_s2"]["uncertainty"]
            and gm["uncertainty_source_id"] == "jpl_satellite_parameters_20261004"
            and gm["uncertainty_primary_source_id"] is None
            and gm["uncertainty_status"] == "reported_aggregator_sigma_unverified",
        "radius_compilation_exact": radius["value"] == source["iau_radius_km"]
            == source["jpl"]["radius_km"]["value"]
            and radius["uncertainty"] == source["iau_radius_error_km"],
        "radius_role_preserved": radius["definition"] == passport["radius_definition"] == "iau_compiled_mean_radius"
            and radius["status"] == "compiled_reference_only"
            and radius["primary_measurement_source_id"] is None,
        "unknown_sigma_and_distribution": all(p["uncertainty_sigma_level"] is None
            and p["uncertainty_distribution"] is None for p in (gm, radius)),
        "units": gm["unit"] == gm["uncertainty_unit"] == "km3 / s2"
            and radius["unit"] == radius["uncertainty_unit"] == "km",
        "source_roles": gm["source_id"] == "jup365_comments_20261004"
            and radius["source_id"] == "archinal2018_wgccre",
        "traceable_parameters": all(all(p.get(k) for k in
            ("source_id", "source_locator", "source_version", "uncertainty_status")) for p in (gm, radius)),
        "reference_field_constants": reference["j2"]["value"] == source["ephemeris_field_constants"]["S3J2"]
            and reference["c22"]["value"] == source["ephemeris_field_constants"]["S3C22"]
            and reference["reference_radius"]["value"] == source["ephemeris_field_constants"]["S3RADEQ"]
            and reference["normalization"] is None and reference["dynamical_usage_authorized"] is False,
        "unknown_shape_not_fabricated": all(passport["shape"][key] is None for key in
            ("axes", "volume_equivalent_radius", "contact_surface")) and passport["pole"] is None,
        "reference_sphere_not_measured_axes": passport["shape"]["iau_reference_axes_km"]
            == [source["iau_radius_km"]] * 3
            and passport["shape"]["reference_axes_status"] == "compiled_same_not_independent_measurements",
        "unknown_covariance_not_zero": passport["covariance"]["matrix"] is None
            and passport["covariance"]["independent_sampling_authorized"] is False
            and all(p["covariance_ref"] is None for p in (gm, radius)),
        "unknown_state_not_fabricated": all(passport["state"][key] is None for key in
            ("epoch", "time_scale", "origin", "frame", "position", "velocity", "source_id")),
        "gravity_not_adopted": all(passport["gravity"][key] is None for key in
            ("j2", "c22", "s22", "normalization", "reference_radius", "source_id")),
        "no_unverified_usage": all(passport["usage"][key] is False for key in
            ("production_integration", "uncertainty_ensemble", "full_W0_handoff", "physical_contact_model")),
    }


def audit_ganymede_archive(archive, source, au_km):
    """Сохранить все 130 стартов; сравнить скаляры, фазы и инварианты отдельно."""
    groups, records, hashes, comparisons = defaultdict(list), {}, [], []
    for index in range(1, 131):
        member = f"Jupiter/GANYMEDE/sim_{index}.txt"
        data = archive.read(member)
        parsed = parse_initial_condition(data.decode("utf-8"))
        massive = [row for row in parsed["states"] if not row["name"].startswith("Sat_")]
        key = json.dumps(massive, sort_keys=True)
        groups[key].append(member)
        records[member] = parsed
        hashes.append(dict(member=member, sha256=hashlib.sha256(data).hexdigest()))
        by_name = {row["name"]: row["values"] for row in massive}
        host, parent = by_name["GANYMEDE"], by_name["JUPITER"]
        ratio = host[0] / parent[0]
        comparisons.append(dict(member=member, archive_radius_km=host[1] * au_km,
            archive_mass_ratio=ratio,
            archive_to_primary_mass_factor=ratio / (source["gm_km3_s2"] / source["parent_gm_km3_s2"])))
    largest = max(len(names) for names in groups.values())
    modes = [key for key, names in groups.items() if len(names) == largest]
    if len(modes) != 1:
        raise ValueError("Модальное состояние не единственно")
    modal = modes[0]
    modal_by_name = {row["name"]: row["values"] for row in json.loads(modal)}
    differences = []
    fields = ("mass", "radius", "x", "y", "z", "vx", "vy", "vz")
    for key, members in groups.items():
        if key == modal:
            continue
        changes, invariants = [], []
        for row in json.loads(key):
            base, value = modal_by_name[row["name"]], row["values"]
            for field, a, b in zip(fields, base, value, strict=True):
                if a != b:
                    changes.append(dict(body=row["name"], field=field, modal=a, observed=b))
            if any(a != b for a, b in zip(base[2:], value[2:], strict=True)):
                modal_norm = math.sqrt(sum(v * v for v in base[2:5])) * au_km
                observed_norm = math.sqrt(sum(v * v for v in value[2:5])) * au_km
                invariants.append(dict(body=row["name"], modal_distance_km=modal_norm,
                    observed_distance_km=observed_norm, delta_distance_km=observed_norm - modal_norm))
        differences.append(dict(members=members, exact_differences=changes, distance_invariants=invariants))
    nonmodal = sorted(member for key, names in groups.items() if key != modal for member in names)
    field_groups = {json.dumps(parsed["fields"], sort_keys=True) for parsed in records.values()}
    return dict(archive_members_checked=len(records), member_hashes=hashes,
        mass_radius_comparisons=comparisons, massive_state_group_counts=sorted(len(v) for v in groups.values()),
        modal_reference_member=groups[modal][0], modal_members=groups[modal], nonmodal_members=nonmodal,
        differences=differences, field_versions=len(field_groups),
        one_submoon_per_input=all(sum(row["name"].startswith("Sat_") for row in p["states"]) == 1
                                 for p in records.values()),
        host_origin_zero=all(all(v == 0 for v in next(row["values"] for row in p["states"]
            if row["name"] == "GANYMEDE")[2:]) for p in records.values()),
        disposition="preserve_all_originals_new_explicit_states_required_no_silent_repair",
        epoch=None, time_scale=None, frame=None, archive_solar_mass_kg=None)
