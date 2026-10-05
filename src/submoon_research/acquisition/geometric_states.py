"""Строгий контракт геометрических векторов Horizons и смена начала координат."""

from __future__ import annotations

import csv
import math
import re
from decimal import Decimal
from io import StringIO

import numpy as np


def build_requests(config):
    """Десять общих векторов и три прямых контроля; без скрытых запросов."""
    def request(target, center):
        return dict(service="horizons", operation="horizons.query", cache=False,
            limit=config["rows_per_query"] + 1, params=dict(target=target, center=center,
            start=config["epoch_calendar"], stop=config["stop_calendar"], step=config["step"],
            time_scale=config["time_scale"], kind="vectors", corrections=config["corrections"],
            ref_plane=config["ref_plane"]))

    jobs = [{"name": name, "role": "ssb", "body": name,
             "request": request(target, config["center"])}
            for name, target in config["targets"].items()]
    jobs.extend({"name": f"direct_{host}", "role": "direct", "body": parent, "host": host,
                 "request": request(config["targets"][parent], f"500@{config['targets'][host]}")}
                for host, parent in config["parents"].items())
    return jobs


def parse_vectors(text, response, request, config):
    """Единицы/оси проверяются по реальному заголовку, не назначаются парсером."""
    if response.get("status") != "success" or response.get("cache_hit") is not False:
        raise ValueError("Нет свежего успешного ответа Horizons")
    if response.get("truncated") or response.get("limit_reached"):
        raise ValueError("Ответ усечён или достигнут локальный предел")
    if response["request"] != request:
        raise ValueError("Сохранённый запрос не совпадает с контрактом")
    params = request["params"]
    effective = response["metadata"]["horizons_parameters"]
    expected = {"COMMAND": params["target"], "CENTER": params["center"],
                "START_TIME": params["start"], "STOP_TIME": params["stop"],
                "STEP_SIZE": params["step"], "EPHEM_TYPE": "VECTORS", "TIME_TYPE": "TDB",
                "REF_SYSTEM": "ICRF", "REF_PLANE": "FRAME", "VEC_CORR": "NONE",
                "OUT_UNITS": "AU-D", "CSV_FORMAT": "YES"}
    if any(effective.get(key) != value for key, value in expected.items()):
        raise ValueError("Параметры Horizons не соответствуют геометрическому контракту")
    before, body = text.split("$$SOE", 1)
    body, _ = body.split("$$EOE", 1)
    for pattern in (r"Output units\s*:\s*AU-D", r"Reference frame\s*:\s*ICRF",
                    r"Output type\s*:\s*GEOMETRIC cartesian states", r"Start time\s*:.*TDB",
                    r"Stop\s+time\s*:.*TDB"):
        if not re.search(pattern, before):
            raise ValueError(f"Отсутствует подтверждение заголовка: {pattern}")
    identities = {}
    for label, expected_id in (("Target", params["target"]),
                               ("Center", params["center"].split("@")[-1])):
        match = re.search(rf"{label} body name:\s*(.*?)\s*\((-?\d+)\)\s*{{source:\s*(.*?)}}", before)
        if not match or match[2] != expected_id:
            raise ValueError(f"Неверный {label} body ID")
        identities[label.lower()] = dict(name=match[1], id=match[2], ephemeris_source=match[3])
    headings = next(line for line in reversed(before.splitlines())
                    if "JDTDB" in line and "," in line)
    columns = [value.strip() for value in next(csv.reader([headings]))]
    fields = ["JDTDB", "X", "Y", "Z", "VX", "VY", "VZ", "RG"]
    if any(columns.count(field) != 1 for field in fields):
        raise ValueError("Неоднозначные или отсутствующие столбцы векторов")
    rows = list(csv.reader(StringIO(body.strip()), skipinitialspace=True))
    if len(rows) != config["rows_per_query"] or response["row_count"] != len(rows):
        raise ValueError("Число моментов не соответствует контракту")
    parsed, times, max_range_error = [], [], 0.0
    for index, row in enumerate(rows):
        if len(row) != len(columns):
            raise ValueError("Неполная строка Horizons")
        values = {field: row[columns.index(field)].strip() for field in fields}
        numbers = {field: float(value) for field, value in values.items()}
        if not all(math.isfinite(value) for value in numbers.values()):
            raise ValueError("Неконечный компонент вектора")
        for field in fields:
            if float(response["rows"][index][field]) != numbers[field]:
                raise ValueError("Таблица AstroBridge расходится с исходным текстом")
        jd = Decimal(values["JDTDB"])
        expected_jd = Decimal(str(config["epoch_jd_tdb"])) + Decimal(index * 60) / Decimal(86400)
        if abs(jd - expected_jd) > Decimal(str(config["validation_tolerances"]["epoch_jd_days"])):
            raise ValueError("Неверная эпоха TDB")
        times.append(float(jd))
        state = [numbers[field] for field in fields[1:7]]
        distance = np.linalg.norm(state[:3])
        range_error = abs(distance - numbers["RG"]) / max(distance, 1e-300)
        max_range_error = max(max_range_error, float(range_error))
        if range_error > config["validation_tolerances"]["source_range_relative"]:
            raise ValueError("Длина вектора не соответствует колонке RG")
        parsed.append(state)
    if times[1] <= times[0]:
        raise ValueError("Моменты повторяются или идут назад")
    raw = np.array(parsed)
    factors = np.array([config["au_km"]] * 3 + [config["au_km"] / config["day_seconds"]] * 3)
    normalized = raw * factors
    roundtrip = float(np.max(np.abs(normalized / factors - raw) / np.maximum(np.abs(raw), 1e-300)))
    if roundtrip > config["validation_tolerances"]["conversion_roundtrip_relative"]:
        raise ValueError("Ошибка обратного преобразования единиц")
    return dict(epoch_jd_tdb=times, states_km_km_s=normalized.tolist(), source=identities,
                max_range_relative_error=max_range_error, conversion_relative_error=roundtrip)


def assemble_states(parsed, config):
    """Относительные состояния; прямой контроль центра для каждой пилотной системы."""
    times = parsed["sun"]["epoch_jd_tdb"]
    if any(item["epoch_jd_tdb"] != times for item in parsed.values()):
        raise ValueError("Несогласованные эпохи тел")
    ssb = {name: np.array(parsed[name]["states_km_km_s"]) for name in config["targets"]}
    relative, metrics, checks = {}, {}, {}
    for host, bodies in config["host_bodies"].items():
        relative[host] = {name: (ssb[name] - ssb[host]).tolist() for name in bodies}
        parent = config["parents"][host]
        direct = np.array(parsed[f"direct_{host}"]["states_km_km_s"])
        delta = np.array(relative[host][parent]) - direct
        position = float(np.max(np.linalg.norm(delta[:, :3], axis=1)))
        velocity = float(np.max(np.linalg.norm(delta[:, 3:], axis=1)))
        metrics[host] = dict(position_max_km=position, velocity_max_km_s=velocity)
        checks[f"{host}_direct_position"] = position <= config["validation_tolerances"]["direct_relative_position_km"]
        checks[f"{host}_direct_velocity"] = velocity <= config["validation_tolerances"]["direct_relative_velocity_km_s"]
        checks[f"{host}_origin_zero"] = bool(np.array_equal(relative[host][host], np.zeros((2, 6))))
    return dict(schema_version="0.1", experiment_id=config["experiment_id"],
        data_kind=config["data_kind"], synthetic=False, production_allowed=False,
        epoch_jd_tdb=times, initial_epoch_index=0, time_scale="TDB", frame="ICRF",
        axes_motion="fixed", position_unit="km", velocity_unit="km/s",
        geometric_corrections="NONE", uncertainty=None,
        uncertainty_status="ephemeris_covariance_not_supplied", covariance_ref=None,
        barycentric_origin="solar_system_barycenter", barycentric= {k: v.tolist() for k, v in ssb.items()},
        host_relative=relative, sources={key: item["source"] for key, item in parsed.items()}), checks, metrics
