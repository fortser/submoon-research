#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Монитор предложений Vast.ai: ловит появление целевых станций и сигнализирует.

Задача: как только на рынке появляется станция с нужным процессором (по умолчанию
CPU-only: Core i9-14900KF, AMD EPYC 9555/9554/9654) по цене в пределах лимита —
вывести сигнал и записать в лог, чтобы пользователь успел арендовать вручную.

ВАЖНО (правила проекта): скрипт НЕ арендует инстансы автоматически. Аренда и любые
платные действия — только после явного согласования плана и бюджета с пользователем.

Особенности (проверено, см. vastai_cli_cheatsheet.md и VastAI_logs/RUN_LOG.md):
- CPU-only (num_gpus=0) не попадают в обычную выдачу search offers [E11] — отдельный запрос;
- cpu_name НЕ поисковый ключ [E9] — фильтрация по подстроке на клиенте;
- у CPU-only cpu_ram=0 (RAM не специфицирован) [N1] — проверять перед арендой;
- вызовы CLI ненадёжны через HTTP(S)-прокси [E1/E8] — снимаем прокси-переменные,
  задаём таймаут, повторяем (до 3 попыток), успех — по отсутствию 'failed with error';
- exit code CLI при ошибках API равен 0 — проверяем вывод по подстрокам [§9 памятки].
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
STATE_FILE = BASE_DIR / "scratch" / "vast_monitor_state.json"
ALERT_LOG = BASE_DIR / "VastAI_logs" / "vast_monitor_alerts.log"

DEFAULT_CPUS = ["14900KF", "9555", "9554", "9654"]   # i9-14900KF, EPYC 9555/9554/9654
DEFAULT_MAX_PRICE = 0.05                              # $/час (dph_total, с 5 GiB storage)
DEFAULT_MIN_CORES = 16                                # cpu_cores_effective минимум
DEFAULT_LIMIT = 200                                   # офферов на запрос (дешёвые первыми)
MAX_RETRIES = 3
CALL_TIMEOUT = 60                                     # сек на один вызов CLI


def pprint(*args, **kw):
    """Печать с защитой от cp1251 на Windows-консоли [W1]."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print(*args, **kw)


def run_search(query: str, limit: int) -> list[dict]:
    """Один вызов vastai search offers с retry и без прокси. Возвращает JSON-список."""
    env = {
        k: v for k, v in os.environ.items()
        if k.upper() not in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    }
    cmd = ["vastai", "search", "offers", query, "--limit", str(limit), "--order", "dph", "--raw"]
    last_err = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  env=env, timeout=CALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            last_err = f"timeout {CALL_TIMEOUT}s"
            pprint(f"[monitor] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        out = (proc.stdout or "").strip()
        # exit code ненадёжен: при 400 CLI всё равно rc=0 [§9]
        if proc.returncode != 0 or "failed with error" in out or "Traceback" in out:
            last_err = f"rc={proc.returncode}, out={out[:200]}"
            pprint(f"[monitor] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        if not out:
            last_err = "пустой вывод"
            pprint(f"[monitor] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
            continue
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            last_err = f"JSON decode: {exc}"
            pprint(f"[monitor] попытка {attempt}/{MAX_RETRIES}: {last_err}")
            time.sleep(3)
    raise RuntimeError(f"search offers не выполнен после {MAX_RETRIES} попыток: {last_err}")


def collect_offers(limit: int, with_gpu: bool) -> list[dict]:
    """CPU-only (num_gpus=0) + при --with-gpu обычная выдача; дедуп по id."""
    queries = ["num_gpus=0"]
    if with_gpu:
        queries.append("")
    seen: dict[int, dict] = {}
    for q in queries:
        for o in run_search(q, limit):
            seen[o["id"]] = o
    return list(seen.values())


def matches(o: dict, cpus: list[str], max_price: float, min_cores: float) -> bool:
    """Фильтр на клиенте: подстрока cpu_name, цена, эффективные ядра.
    num_gpus=0 обеспечивается самим запросом (для --with-gpu не фильтруем)."""
    cpu_name = o.get("cpu_name") or ""
    return (
        o.get('resource_type') in ('cpu', 'compute', 'gpu')
        and any(p.lower() in cpu_name.lower() for p in cpus)
        and o.get("dph_total", 1e9) <= max_price
        and o.get("cpu_cores_effective", 0) >= min_cores
    )


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


def alert(o: dict) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (f"HIT {ts} id={o['id']} {o.get('cpu_name') or '?'} "
            f"price={o['dph_total']:.4f}$/ч эфф.яд={o.get('cpu_cores_effective')} "
            f"диск={o.get('disk_space', 0):.0f}GiB num_gpus={o.get('num_gpus')} "
            f"loc={o.get('geolocation', '?')}")
    pprint("\033[92m" + "=" * 70 + "\033[0m")
    pprint("\033[92m*** НАЙДЕНА ЦЕЛЕВАЯ СТАНЦИЯ ***\033[0m")
    pprint(line)
    pprint("Аренда вручную (только после согласования плана и бюджета с пользователем!):")
    pprint(f"  vastai create instance {o['id']} --image <образ> --disk <GiB> --ssh --direct "
           f"--cancel-unavail")
    pprint("\033[92m" + "=" * 70 + "\033[0m")
    try:
        sys.stdout.write("\a")  # звуковой сигнал
        sys.stdout.flush()
    except Exception:
        pass
    ALERT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with ALERT_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Монитор предложений Vast.ai (по умолчанию CPU-only)")
    ap.add_argument("--interval", type=float, default=60.0, help="сек между проверками (default 60)")
    ap.add_argument("--once", action="store_true", help="один цикл проверки и выход")
    ap.add_argument("--max-price", type=float, default=DEFAULT_MAX_PRICE, help=f"лимит $/час (default {DEFAULT_MAX_PRICE})")
    ap.add_argument("--min-cores", type=float, default=DEFAULT_MIN_CORES, help=f"мин cpu_cores_effective (default {DEFAULT_MIN_CORES})")
    ap.add_argument("--cpu", action="append", default=DEFAULT_CPUS, help="подстрока в cpu_name (повторяемый)")
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"офферов на запрос (default {DEFAULT_LIMIT})")
    ap.add_argument("--with-gpu", action="store_true", help="учитывать и станции с GPU (доп. обычный запрос)")
    args = ap.parse_args()

    state = load_state()
    seen = state.setdefault("seen", {})
    alerts = state.setdefault("alerts", {})

    pprint(f"[monitor] старт: cpu={args.cpu} max_price={args.max_price} "
           f"min_cores={args.min_cores} with_gpu={args.with_gpu} interval={args.interval}s")

    while True:
        t0 = time.time()
        try:
            offers = collect_offers(args.limit, args.with_gpu)
            hits = [o for o in offers if matches(o, args.cpu, args.max_price, args.min_cores)]
            new = [o for o in hits if str(o["id"]) not in alerts]
            pprint(f"[monitor] {datetime.now():%H:%M:%S} офферов={len(offers)} подходящих={len(hits)} новых={len(new)}")
            for o in new:
                alert(o)
                alerts[str(o["id"])] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            seen.update({str(o["id"]): {"cpu": o.get("cpu_name"), "price": o.get("dph_total"),
                                        "first_seen": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
                         for o in hits})
            save_state(state)
        except Exception as exc:
            pprint(f"[monitor] ошибка цикла: {exc}")
        if args.once:
            break
        time.sleep(max(1.0, args.interval - (time.time() - t0)))


if __name__ == "__main__":
    main()
