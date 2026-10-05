"""Локальный аудит текста и входов статьи; динамические исходы не вычисляются."""

from __future__ import annotations

import html
import csv
import hashlib
import math
import re
from collections import defaultdict
from zipfile import ZipFile

import numpy as np



def tex_number(value):
    """Читать только десятичное число или явный TeX a * 10**b; без eval."""
    value = re.sub(r"[\s$]", "", value)
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)", value)
    if match:
        return float(match[1])
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\\(?:times|cdot)10\^\{([+-]?\d+)\}", value)
    if not match:
        raise ValueError(f"Unsupported explicit numeric notation: {value}")
    result = float(f"{match[1]}e{match[2]}")
    if not math.isfinite(result):
        raise ValueError("Non-finite source number")
    return result


def sphere_mass(radius_m, density_kg_m3):
    if any(not math.isfinite(x) or x <= 0 for x in (radius_m, density_kg_m3)):
        raise ValueError("Radius and density must be positive finite SI quantities")
    return (4 * math.pi / 3) * density_kg_m3 * radius_m**3


def strip_tex_comments(text):
    return re.sub(r"(?<!\\)%[^\n]*", "", text)


def bundle_inventory(root, bundle, ledger=None):
    directory = root / bundle
    def content(path):
        return ledger.read(path.relative_to(root).as_posix(), registered=True) if ledger else path.read_bytes()
    tex_path = directory / "aa57101-25.tex"
    text = content(tex_path).decode('utf-8')
    active = strip_tex_comments(text)
    figures = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", active)
    bibliography = re.findall(r"\\bibliography\{([^}]+)\}", active)
    classes = re.findall(r"\\documentclass(?:\[[^\]]*\])?\{([^}]+)\}", active)
    styles = re.findall(r"\\bibliographystyle\{([^}]+)\}", active)
    expected = set(figures + ["00README.json", "aa57101-25.tex"])
    expected.update(name + ".bib" for names in bibliography for name in names.split(","))
    expected.update(name + ".cls" for name in classes)
    expected.update(name + ".bst" for name in styles)
    missing = sorted(name for name in expected if not (directory / name).is_file())
    inventory = [{"path": path.relative_to(root).as_posix(), "bytes": len(content(path)),
                  "sha256": hashlib.sha256(content(path)).hexdigest()} for path in sorted(directory.rglob("*")) if path.is_file()]
    pdf_headers = {name: content(directory / name).startswith(b"%PDF-")
                   for name in figures if (directory / name).is_file()}
    bib_text = content(directory / 'sources.bib').decode('utf-8')
    bib_keys = set(re.findall(r"@\w+\s*\{\s*([^,\s]+)", strip_tex_comments(bib_text)))
    cited = {key.strip() for group in re.findall(r"\\cite\w*(?:\[[^\]]*\])*\{([^}]+)\}", active)
             for key in group.split(",")}
    checks = {"explicit_bundle_files_present": not missing,
              "referenced_pdf_headers": bool(pdf_headers) and all(pdf_headers.values()),
              "bibliography_keys_present": not (cited - bib_keys),
              "complete_document_and_appendix": all(token in active for token in
                  (r"\begin{document}", r"\end{document}",
                   r"\begin{appendix}", r"\end{appendix}"))}
    return {"files": inventory, "referenced_figures": figures, "missing_files": missing,
            "missing_citation_keys": sorted(cited - bib_keys), "checks": checks,
            "identity": {"title": re.search(r"\\title\{([^}]+)\}", active)[1],
                         "authors": ["R. Dahoumane", "V. Lainey", "K. Baillié"],
                         "version_label": "arXiv:2609.03564v1",
                         "version_basis": "user_supplied_directory_name",
                         "external_version_identity_verified": False}}, text


def paper_physical_parameters(text):
    parameters = {}
    pattern = r"^\s*(\w+)\s*&\s*([\d.]+)\s*&\s*([^&]+)&\s*(.*?)\\\\"
    for match in re.finditer(pattern, text, re.MULTILINE):
        name, radius, mass, source = match.groups()
        try:
            value = tex_number(mass)
        except ValueError:
            continue
        parameters[name] = {"mass_kg": value, "radius_km": float(radius),
                            "mass_source_tex": source.strip(),
                            "source_line": text[:match.start()].count("\n") + 1,
                            "uncertainty": None, "uncertainty_status": "not_reported_in_table"}
    return parameters


def html_rows(text):
    """Прочитать явные HTML-строки сохранённой таблицы; не запускать JS."""
    rows = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", text, re.DOTALL | re.IGNORECASE):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        cleaned = [" ".join(html.unescape(re.sub(r"<[^>]+>", "", cell)).split())
                   for cell in cells]
        rows.append(cleaned)
    return rows


def jpl_parameter_rows(text, names):
    results = {}
    for cells in html_rows(text):
        if cells and cells[0] in names:
            values = []
            for cell in cells[1:4]:
                match = re.match(r"([\d.]+)\s*±\s*([\d.]+)", cell)
                if not match:
                    raise ValueError(f"Missing value/uncertainty in JPL row {cells[0]}: {cell}")
                values.append({"value": float(match[1]), "uncertainty": float(match[2]),
                               "source_text": cell})
            results[cells[0]] = dict(zip(("gm_km3_s2", "radius_km", "density_g_cm3"), values))
    if set(results) != set(names):
        raise ValueError("Requested JPL rows were not found")
    return results


def numeric_audit(paper, jpl, g_si):
    submoon_mass = sphere_mass(100.0, 1000.0)
    comparisons = {}
    for name, reference in jpl.items():
        entry = paper[name]
        gm_si = reference["gm_km3_s2"]["value"] * 1e9
        reference_mass = gm_si / g_si
        comparisons[name] = {
            "paper_mass_kg": entry["mass_kg"], "paper_radius_km": entry["radius_km"],
            "paper_implied_density_kg_m3": entry["mass_kg"] / sphere_mass(entry["radius_km"] * 1000, 1),
            "reference_mass_kg": reference_mass,
            "mass_ratio_paper_to_reference": entry["mass_kg"] / reference_mass,
            "reference_gm_km3_s2": reference["gm_km3_s2"],
            "reference_radius_km": reference["radius_km"],
            "reference_density_g_cm3": reference["density_g_cm3"],
            "status": "table_comparison_not_actual_integrator_input",
            "mass_uncertainty": None,
            "mass_uncertainty_status": "not_propagated_use_primary_GM_and_covariance_in_passports"}
    return {"submoon": {"radius_m": 100.0, "density_kg_m3": 1000.0,
                        "derived_mass_kg": submoon_mass, "paper_appendix_mass_kg": 4.9e9,
                        "relative_appendix_mass_difference": 4.9e9 / submoon_mass - 1,
                        "uncertainty": None, "uncertainty_status": "deterministic_model_assumption"},
            "hill_radius_ratio_without_to_with_factor3": 3**(1 / 3),
            "comparisons": comparisons,
            "limitations": "Сопоставление таблиц не устанавливает использованные в интеграторе входы."}


def parse_initial_condition(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "The units used are AU, Sun Mass, Day and rad":
        raise ValueError("Initial-condition units are absent or unsupported")
    state_header = "Name,Mass,Radius,X,Y,Z,VX,VY,VZ"
    field_header = "Body,J2,x_rot,y_rot,z_rot"
    if state_header not in lines or field_header not in lines:
        raise ValueError("Initial-condition sections are missing")
    states, fields = [], []
    for header, target, width in [(state_header, states, 9), (field_header, fields, 5)]:
        for line in lines[lines.index(header) + 1:]:
            if line.startswith("#"):
                break
            cells = next(csv.reader([line]))
            if len(cells) != width:
                raise ValueError("Invalid source row width")
            values = [float(value) for value in cells[1:]]
            if not all(math.isfinite(value) for value in values):
                raise ValueError("Non-finite initial condition")
            target.append({"name": cells[0], "values": values})
    if not states or not fields or len({row["name"] for row in states}) != len(states):
        raise ValueError("Empty or duplicate-body initial conditions")
    if any(row["values"][0] <= 0 or row["values"][1] < 0 for row in states):
        raise ValueError("Invalid initial mass/radius")
    return {"states": states, "fields": fields,
            "units": {"mass": "solar_mass", "length": "au", "time": "day", "angle": "rad"}}


def archive_audit(path, pilot_names):
    grouped = defaultdict(list)
    members = []
    representative = {}
    with ZipFile(path) as archive:
        bad_crc = archive.testzip()
        for info in archive.infolist():
            if info.is_dir():
                continue
            parts = info.filename.split("/")
            raw = archive.read(info)
            members.append({"member": info.filename, "bytes": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest()})
            if len(parts) == 3 and re.fullmatch(r"sim_\d+\.txt", parts[2]):
                parsed = parse_initial_condition(raw.decode("utf-8"))
                grouped[parts[1]].append((info.filename, parsed))
                if parts[2] == "sim_1.txt":
                    representative[parts[1]] = {"member": info.filename, **parsed}
        readme = archive.read("README.txt").decode("utf-8")
    checks = {"zip_crc": bad_crc is None, "hosts_present": bool(grouped),
              "each_host_has_130_unique_indices": True, "one_submoon_per_input": True,
              "host_origin_zero": True, "fixed_massive_states_per_host": True,
              "normalized_planet_axes": True}
    hosts = {}
    for host, records in sorted(grouped.items()):
        checks["each_host_has_130_unique_indices"] &= {
            int(re.search(r"sim_(\d+)\.txt", name)[1]) for name, _ in records} == set(range(1, 131))
        sub_masses, sub_radii, radii, inclinations, inferred_g = [], [], [], [], []
        massive_sets, field_sets = set(), set()
        massive_records = defaultdict(list)
        sub_counts = []
        for _, parsed in records:
            body = next(row for row in parsed["states"] if row["name"] == host)
            subs = [row for row in parsed["states"] if row["name"].startswith("Sat_")]
            sub_counts.append(len(subs))
            checks["one_submoon_per_input"] &= len(subs) == 1
            checks["host_origin_zero"] &= all(value == 0 for value in body["values"][2:])
            massive_key = json_key([row for row in parsed["states"] if row not in subs])
            massive_sets.add(massive_key)
            massive_records[massive_key].append(_)
            field_sets.add(json_key(parsed["fields"]))
            for field in parsed["fields"]:
                checks["normalized_planet_axes"] &= abs(np.linalg.norm(field["values"][1:]) - 1) < 1e-12
            if len(subs) != 1:
                continue
            sub = subs[0]["values"]
            r, v = np.array(sub[2:5]), np.array(sub[5:8])
            planet_name = "JUPITER" if records[0][0].startswith("Jupiter/") else "SATURN"
            planet = next(row for row in parsed["states"] if row["name"] == planet_name)["values"]
            rp, vp = np.array(planet[2:5]), np.array(planet[5:8])
            hs, hp = np.cross(r, v), np.cross(rp, vp)
            inclinations.append(math.degrees(math.acos(float(np.clip(
                np.dot(hs, hp) / (np.linalg.norm(hs) * np.linalg.norm(hp)), -1, 1)))))
            radius = float(np.linalg.norm(r))
            radii.append(radius)
            inferred_g.append(float(np.dot(v, v)) * radius / (body["values"][0] + sub[0]))
            sub_masses.append(sub[0])
            sub_radii.append(sub[1])
        checks["fixed_massive_states_per_host"] &= len(massive_sets) == 1 and len(field_sets) == 1
        first = representative[host]
        host_values = next(row for row in first["states"] if row["name"] == host)["values"]
        hosts[host] = {"planet": planet_name, "files": len(records), "submoon_counts": sorted(set(sub_counts)),
                       "host_mass_solar": host_values[0], "host_radius_au": host_values[1],
                       "submoon_mass_solar_range": [min(sub_masses), max(sub_masses)],
                       "submoon_radius_au_range": [min(sub_radii), max(sub_radii)],
                       "circular_radius_min_max_au": [min(radii), max(radii)],
                       "inclination_min_max_deg": [min(inclinations), max(inclinations)],
                       "inferred_circular_g_range_au3_solar_day2": [min(inferred_g), max(inferred_g)],
                       "massive_bodies": [row["name"] for row in first["states"]
                                          if not row["name"].startswith("Sat_")],
                       "fields": first["fields"],
                       "massive_state_versions": len(massive_sets),
                       "field_versions": len(field_sets)}
        modal_key = max(massive_records, key=lambda key: len(massive_records[key]))
        hosts[host]["modal_massive_state_files"] = len(massive_records[modal_key])
        hosts[host]["nonmodal_massive_state_files"] = sorted(
            name for key, names in massive_records.items() if key != modal_key for name in names)
        if host in pilot_names + ["HYPERION"]:
            g_inferred = float(np.median(inferred_g))
            modal_name = massive_records[modal_key][0]
            modal_input = next(parsed for name, parsed in records if name == modal_name)
            planet = next(row for row in modal_input["states"] if row["name"] == planet_name)["values"]
            rp, vp = np.array(planet[2:5]), np.array(planet[5:8])
            mu = g_inferred * (host_values[0] + planet[0])
            planet_r = float(np.linalg.norm(rp))
            a = 1 / (2 / planet_r - float(np.dot(vp, vp)) / mu)
            e = float(np.linalg.norm(np.cross(vp, np.cross(rp, vp)) / mu - rp / planet_r))
            rh0 = a * (host_values[0] / (3 * planet[0]))**(1 / 3)
            rh3 = (1 - e) * rh0
            upper = max(radii) / 0.9309
            hosts[host]["grid_diagnostics"] = {
                "assumption": "Круговые старты e=0 из статьи; G восстановлен из v^2*r/(m_host+m_sub).",
                "massive_state_reference_member": modal_name,
                "inferred_g": g_inferred, "host_a_au": a, "host_e": e,
                "hill_circular_with3_au": rh0,
                "hill_pericentre_with3_au": rh3,
                "inferred_hill_from_upper_grid_au": upper,
                "upper_grid_hill_ratio_to_with3": upper / rh3,
                "upper_grid_hill_ratio_to_circular_with3": upper / rh0,
                "upper_grid_hill_ratio_to_without3": upper / (rh3 * 3**(1 / 3)),
                "radius_grid_au": sorted({round(value, 17) for value in radii}),
                "inclination_grid_deg": sorted({round(value, 5) for value in inclinations}),
                "radial_velocity_max_au_day": max(abs(float(np.dot(
                    np.array(row["values"][2:5]), np.array(row["values"][5:8])))
                    / np.linalg.norm(row["values"][2:5]))
                    for _, parsed in records for row in parsed["states"]
                    if row["name"].startswith("Sat_"))}
    return {"checks": {key: bool(value) for key, value in checks.items()}, "host_count": len(hosts),
            "input_file_count": sum(len(records) for records in grouped.values()),
            "members": members, "hosts": hosts, "readme": readme,
            "representative_inputs": {name: representative[name] for name in
                                      pilot_names + ["HYPERION", "PHOEBE"] if name in representative},
            "code_files": [row["member"] for row in members
                           if not row["member"].endswith(".txt")],
            "limitations": ["Чтение опубликованных входов не доказывает запуск именно этой версии авторского кода.",
                            "G и элементы хозяина здесь диагностически восстановлены при явно указанном допущении.",
                            "Эпоха, шкала времени, название глобальных осей и солнечная единица массы в кг не записаны в файлах."]}


def json_key(value):
    import json

    return json.dumps(value, sort_keys=True)
