#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Трекинг доступности и стоимости CPU-only станций Vast.ai по списку из good_cpu.txt.

По каждому циклу:
  - ищет CPU-only офферы (num_gpus=0, доступные к аренде) с cpu_name, совпадающим
    с паттернами, ВЫВЕДЕННЫМИ ИЗ good_cpu.txt (источник правды — файл);
  - условия отбора: ЭФФЕКТИВНЫЕ ЯДРА == ФИЗИЧЕСКИЕ (доли машины пропускаются), диск >= 4 GiB;
  - печатает таблицу: id, модель, $/час, эффективные/физические ядра, RAM, диск, локация;
  - детектирует события: ПОЯВИЛАСЬ / УШЛА (нет N=2 цикла подряд) / ИЗМЕНЕНИЕ цены
    или физ. параметров — пишет в VastAI_logs/vast_track_events.log;
  - историю снимков копит в scratch/vast_track_history.jsonl (JSONL, по циклу),
    состояние — scratch/vast_track_state.json.

Правило проекта: скрипт НЕ арендует инстансы — только наблюдает и сообщает.
Замечания (см. VastAI_logs/RUN_LOG.md): [E1/E8] прокси+таймауты, [E9] cpu_name не
поисковый ключ — фильтр на клиенте, [E11] CPU-only только через num_gpus=0,
[N1] у CPU-only cpu_ram=0 (RAM не специфицирован), [E12] cpu_name бывает None.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
GOOD_CPU = BASE_DIR / "good_cpu.txt"
STATE_FILE = BASE_DIR / "scratch" / "vast_track_state.json"
HISTORY_FILE = BASE_DIR / "scratch" / "vast_track_history.jsonl"
EVENTS_LOG = BASE_DIR / "VastAI_logs" / "vast_track_events.log"
REPORTS_DIR = BASE_DIR / "results"

MAX_RETRIES = 3
CALL_TIMEOUT = 60          # сек на один вызов CLI
GONE_THRESHOLD = 2         # циклов отсутствия до события «УШЛА»
TAIL_WINDOW = 40           # контекст перед токеном для поиска бренда

TOKEN_RE = re.compile(r"\b(\d{2,5}[A-Za-z0-9X]*)\b")
BRAND_ANCHORS = [
    r"Core Ultra \d+", r"Core i[579]-", r"Ryzen \d+", r"Threadripper PRO",
    r"Threadripper", r"EPYC", r"i[579]-", r"Ultra \d+",
]


def pprint(*args, **kw):
    """Печать с защитой от cp1251 на Windows-консоли [W1]."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(*args, **kw)


def run_search() -> list[dict]:
    """Один вызов vastai search offers 'num_gpus=0' с retry и без прокси."""
    env = {
        k: v for k, v in os_environ_without_proxy().items()
    }
    cmd = ["vastai", "search", "offers", "num_gpus=0", "--limit", "400", "--order", "dph", "--raw"]
    last_err = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  env=env, timeout=CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_err = f"timeout {CALL_TIMEOUT}s"
            pprint(f"[track] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        out = (proc.stdout or "").strip()
        if proc.returncode != 0 or "failed with error" in out or "Traceback" in out:
            last_err = f"rc={proc.returncode}, out={out[:200]}"
            pprint(f"[track] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        if not out:
            last_err = "пустой вывод"
            pprint(f"[track] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            last_err = f"JSON decode: {exc}"
            pprint(f"[track] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
    raise RuntimeError(f"search offers не выполнен после {MAX_RETRIES} попыток: {last_err}")


def os_environ_without_proxy() -> dict:
    return {k: v for k, v in __import__("os").environ.items()
            if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}


def read_good_cpu() -> list[str]:
    """Строки-источники моделей из good_cpu.txt (первая таб-колонка, без заголовков)."""
    lines: list[str] = []
    for raw in GOOD_CPU.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" in line:
            line = line.split("\t", 1)[0].strip()
        if not line or line.startswith(("Группа", "Процессор", "Это", "#")) or "Комментарий" in line:
            continue
        lines.append(line)
    return lines


def _brand_and_sep(prefix: str, tok: str):
    """Определяет бренд-фразу по тексту ДО токена. Возвращает (brand, sep).
    sep: "-" (Core i9-14900K), " " (Ryzen 9 9950X), "" (семейство: токен не нужен)."""
    # семейные правила (по всему префиксу)
    if re.search(r"Xeon W-", prefix):
        return f"Xeon W-{tok[:2]}", ""                     # W-2400/W-3400 → Xeon W-24 / W-34
    if "EPYC" in prefix and re.fullmatch(r"4\d{3}", tok):
        return "EPYC 4", ""                                # серия 4004/4005 → семейство EPYC 4
    # поиск бренда в ближайшем контексте, при отсутствии — во всём префиксе
    window = prefix[-TAIL_WINDOW:] if len(prefix) > TAIL_WINDOW else prefix
    best = None
    for pat in BRAND_ANCHORS:
        for m in re.finditer(pat, window):
            if best is None or m.end() > best.end():
                best = m
    if best is None:
        for pat in BRAND_ANCHORS:
            for m in re.finditer(pat, prefix):
                if best is None or m.end() > best.end():
                    best = m
    if best is None:
        return None, ""
    a = best.group(0)
    if a.startswith("i9-"):
        return "Core i9", "-"
    if a.startswith("i7-"):
        return "Core i7", "-"
    if a.startswith("i5-"):
        return "Core i5", "-"
    if a.startswith("Core Ultra"):
        return a, " "
    if a.startswith("Ultra"):
        return "Core " + a, " "
    if a.startswith(("EPYC", "Xeon", "Threadripper", "Ryzen")):
        return a.rstrip(" -"), " "
    return None, ""


def derive_patterns(lines: list[str]) -> list[str]:
    """Из строк good_cpu.txt строит паттерны подстрок cpu_name (E9: фильтр на клиенте)."""
    pats: set[str] = set()
    for line in lines:
        for m in TOKEN_RE.finditer(line):
            tok = m.group(1)
            if len(tok) < 3:
                continue
            brand, sep = _brand_and_sep(line[: m.start()], tok)
            if not brand:
                continue
            if sep == "-":
                pats.add(brand + "-" + tok)
            elif sep == " ":
                pats.add(brand + " " + tok)
            else:
                pats.add(brand)                              # семейство, токен не нужен
    # дедупликация: оставляем только «минимальные» паттерны (более длинные содержат короткий)
    ordered = sorted(pats, key=len)
    out: list[str] = []
    for p in ordered:
        if not any(q != p and q in p for q in out):
            out.append(p)
    return out


def matches(o: dict, patterns: list[str], min_disk: float = 4.0) -> bool:
    """Отбор станции: паттерн cpu_name, доступна, диск >= min_disk, целая машина
    (эфф. ядра == физ. ядра — доли с урезанным cpu_cores_effective пропускаем)."""
    cpu_name = o.get("cpu_name") or ""
    # Исправление 2026-10-06: num_gpus=0 возвращает также дисковые тома.
    # CPU хоста диска не является арендуемым вычислительным ресурсом.
    if o.get('resource_type') not in ('cpu', 'compute'):
        return False
    if o.get("num_gpus", 1) != 0:
        return False                                # только станции без видеокарт
    if not any(p in cpu_name for p in patterns):
        return False
    if o.get("rented", False):
        return False
    if (o.get("disk_space") or 0) < min_disk:
        return False
    eff = o.get("cpu_cores_effective") or 0
    phys = o.get("cpu_cores") or 0
    return abs(eff - phys) <= 0.01  # эффективные ядра должны равняться физическим


def fmt_params(o: dict) -> dict:
    return {
        "id": o["id"],
        "cpu": o.get("cpu_name") or "",
        "price": round(o.get("dph_total", 0.0), 4),
        "eff_cores": o.get("cpu_cores_effective"),
        "phys_cores": o.get("cpu_cores"),
        "ram_gib": round((o.get("cpu_ram") or 0) / 1024, 0),
        "disk_gib": round(o.get("disk_space") or 0, 0),
        "loc": o.get("geolocation") or "",
        "num_gpus": o.get("num_gpus"),
        "resource_type": o.get("resource_type"),
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def log_event(line: str) -> None:
    EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def print_table(rows: list[dict], changes: set[int]) -> None:
    pprint("")
    pprint(f"{'#':>3} | {'ID':>10} | {'$/час':>7} | {'эфф.яд':>6} | {'физ.яд':>6} | {'RAM GiB':>7} | {'диск':>8} | Локация | CPU | событие")
    pprint("-" * 120)
    for i, r in enumerate(rows, 1):
        ev = ""
        if r["id"] in changes:
            ev = "<- НОВАЯ"
        pprint(f"{i:>3} | {r['id']:>10} | {r['price']:>7.4f} | {r['eff_cores']:>6.1f} | "
               f"{r['phys_cores']:>6} | {r['ram_gib']:>7.0f} | {r['disk_gib']:>8.0f} | "
               f"{r['loc']:<40} | {r['cpu']:<40} | {ev}")
    pprint("")


def save_report(rows: list[dict], patterns: list[str], when: str, summary: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = REPORTS_DIR / f"vast_cpu_track_{when.replace(':','')}.txt"
    latest = REPORTS_DIR / "vast_cpu_track_latest.txt"
    lines = ["=" * 120,
             "VAST.AI — CPU-only станции по списку good_cpu.txt (доступность + цена + параметры)",
             f"Снимок: {when} | num_gpus=0 | только доступные | паттернов: {len(patterns)}",
             summary,
             "Паттерны (из good_cpu.txt): " + ", ".join(patterns)]
    lines.append("-" * 120)
    lines.append(f"{'#':>3} | {'ID':>10} | {'$/час':>7} | {'эфф.яд':>6} | {'физ.яд':>6} | "
                 f"{'RAM GiB':>7} | {'диск':>8} | Локация | CPU")
    lines.append("-" * 120)
    for i, r in enumerate(rows, 1):
        lines.append(f"{i:>3} | {r['id']:>10} | {r['price']:>7.4f} | {r['eff_cores']:>6.1f} | "
                     f"{r['phys_cores']:>6} | {r['ram_gib']:>7.0f} | {r['disk_gib']:>8.0f} | "
                     f"{r['loc']:<35} | {r['cpu']}")
    body = "\n".join(lines) + "\n"
    fname.write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")
    pprint(f"[track] отчёт сохранён: {fname}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Трекер CPU-only станций Vast.ai по good_cpu.txt")
    ap.add_argument("--min-disk", type=float, default=4.0, help="мин. диск GiB (default 4)")
    ap.add_argument("--once", action="store_true", help="один снимок (с печатью паттернов и отчётом)")
    ap.add_argument("--interval", type=float, default=300.0, help="сек между циклами (default 300)")
    ap.add_argument("--print-patterns", action="store_true", help="только показать паттерны и выйти")
    args = ap.parse_args()

    src_lines = read_good_cpu()
    patterns = derive_patterns(src_lines)
    pprint(f"[track] good_cpu.txt: строк-источников={len(src_lines)}, паттернов={len(patterns)}")
    pprint("[track] паттерны: " + ", ".join(patterns))
    if args.print_patterns:
        return

    state = load_state()
    changes: set[int] = set()
    first_run = not state.get("bootstrapped", False)

    while True:
        when = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            offers = run_search()
            rows = [fmt_params(o) for o in offers if matches(o, patterns, args.min_disk)]
            current_ids = {r["id"] for r in rows}
            changes = set()

            # события
            for r in rows:
                sid = str(r["id"])
                prev = state.get("active", {}).get(sid)
                if prev is None:
                    if not first_run:
                        changes.add(r["id"])
                        log_event(f"EVENT {when} ПОЯВИЛАСЬ id={r['id']} {r['cpu']} "
                                  f"price={r['price']:.4f} эфф.яд={r['eff_cores']} {r['loc']}")
                    state.setdefault("active", {})[sid] = dict(r, gone_count=0, first_seen=when)
                else:
                    if abs(prev.get("price", 0) - r["price"]) > 1e-4:
                        log_event(f"EVENT {when} ЦЕНА id={r['id']} {r['cpu']} "
                                  f"{prev.get('price', 0):.4f} -> {r['price']:.4f} $/ч")
                    if (prev.get("ram_gib") != r["ram_gib"] or prev.get("disk_gib") != r["disk_gib"]
                            or prev.get("loc") != r["loc"] or prev.get("eff_cores") != r["eff_cores"]):
                        log_event(f"EVENT {when} ПАРАМЕТРЫ id={r['id']} {r['cpu']}: "
                                  f"эфф.яд {prev.get('eff_cores')}->{r['eff_cores']} "
                                  f"RAM {prev.get('ram_gib')}->{r['ram_gib']}GiB "
                                  f"диск {prev.get('disk_gib')}->{r['disk_gib']}GiB {prev.get('loc')}->{r['loc']}")
                    prev.update(dict(r, gone_count=0))
                    prev["last_seen"] = when

            active = state.get("active", {})
            for sid, rec in list(active.items()):
                if int(sid) in current_ids:
                    continue
                rec["gone_count"] = rec.get("gone_count", 0) + 1
                if rec["gone_count"] >= GONE_THRESHOLD and not rec.get("reported_gone"):
                    log_event(f"EVENT {when} УШЛА id={sid} {rec.get('cpu')} "
                              f"(не доступна {GONE_THRESHOLD} цикла) price={rec.get('price')} {rec.get('loc')}")
                    rec["reported_gone"] = True

            # история снимков
            with HISTORY_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": when,
                                    "n_offers": len(rows),
                                    "offers": rows}, ensure_ascii=False) + "\n")

            state["bootstrapped"] = True
            save_state(state)

            summary = (f"Найдено: {len(rows)} станций (CPU-only, диск>={args.min_disk:.1f} GiB, "
                       f"эфф. ядра == физ. ядра) | изменений: {len(changes)}")
            pprint(f"[track] {when} | офферов всего={len(offers)} | подходящих={len(rows)} | "
                   f"новых={len(changes)}")
            print_table(rows, changes)
            if args.once:
                save_report(rows, patterns, when, summary)
                return
        except Exception as exc:
            pprint(f"[track] ошибка цикла: {exc}")
        time.sleep(max(1.0, args.interval))


if __name__ == "__main__":
    main()
