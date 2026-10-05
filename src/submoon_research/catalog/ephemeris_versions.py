"""Сопоставление констант конкретных блоков эфемерид, без вывода о динамике."""
from __future__ import annotations

import re
from decimal import Decimal


def body_summary(text: str, version: str, name: str, body_id: int) -> dict:
    """Выбрать блоки с телом; не смешивать константы соседних групп/сборок."""
    headers = list(re.finditer(r"^Satellite Ephemeris:\s*(\S+)\s*$", text, re.M))
    rows = []
    for index, header in enumerate(headers):
        if header[1] != version:
            continue
        stop = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[header.end():stop]
        match = re.findall(
            rf"^\s*{re.escape(name)}\s+{body_id}\s+(\S+)\s+\d+\s+\d+\s+SATORBINT\s*$",
            block, re.M,
        )
        if not match:
            continue
        if len(match) != 1:
            raise ValueError("Неоднозначная строка тела в блоке")
        gm = Decimal(match[0])
        if not gm.is_finite() or gm <= 0:
            raise ValueError("Некорректный GM")
        constants_text = block.split("Additional Constants on the File:", 1)[1]
        constants_text = constants_text.split("Planet gravitational harmonics", 1)[0]
        constants = {}
        for key, value in re.findall(r"\b([A-Z0-9_]+)\s+([-+]?\d\.\d+E[-+]\d+)", constants_text):
            number = Decimal(value)
            if not number.is_finite() or (key in constants and constants[key] != str(number)):
                raise ValueError("Неоднозначная или неконечная константа")
            constants[key] = str(number)
        planetary = re.search(r"Planetary Ephemeris Number:\s*(\S+)", block)
        timespan = re.search(r"Timespan from JED\s+(\S+)\([^\n]+?to JED\s+(\S+)\(", block)
        if planetary is None or timespan is None:
            raise ValueError("Не найден контракт блока")
        rows.append(dict(gm=str(gm), gm_unit="km3/s2", constants=constants,
                         planetary_ephemeris=planetary[1],
                         start_jed=float(timespan[1]), end_jed=float(timespan[2]),
                         summary_line=text[:header.start()].count("\n") + 1))
    if not rows:
        raise ValueError("Блок с указанным телом не найден")
    if len({row['gm'] for row in rows}) != 1 or len({row['planetary_ephemeris'] for row in rows}) != 1:
        raise ValueError("Конфликт прямого/обратного блоков")
    if any(row['constants'] != rows[0]['constants'] for row in rows):
        raise ValueError("Конфликт констант выбранного тела")
    return dict(version=version, body_id=body_id, name=name, gm=rows[0]['gm'],
                constants=rows[0]['constants'], planetary_ephemeris=rows[0]['planetary_ephemeris'],
                blocks=rows)


def compare_himalia_versions(old: str, new: str) -> dict:
    before = body_summary(old, "JUP344", "Himalia", 506)
    after = body_summary(new, "JUP347", "Himalia", 506)
    comparisons = {}
    for key in ("500GM", "501GM", "502GM", "503GM", "504GM", "GM6", "GM10", "J502", "RADIUS"):
        left, right = Decimal(before['constants'][key]), Decimal(after['constants'][key])
        comparisons[key] = dict(old=str(left), new=str(right), delta=str(right - left),
                                relative_delta=str((right - left) / left),
                                unit="1" if key == "J502" else "km" if key == "RADIUS" else "km3/s2")
    return dict(old=before, new=after,
                gm_exact_equal=Decimal(before['gm']) == Decimal(after['gm']),
                gm_delta_km3_s2=str(Decimal(after['gm']) - Decimal(before['gm'])),
                comparisons=comparisons,
                dynamic_compatibility="not_demonstrated", state_delta=None,
                state_delta_status="SPK_not_acquired_no_same_solution_comparison")
