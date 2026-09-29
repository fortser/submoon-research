"""Local commands; no network requests or paid services are invoked here."""

import argparse
import json
import shutil
from pathlib import Path

import yaml

from submoon_research import tracking
from submoon_research.workflows.smoke import environment, run_smoke, sha256, write_json


def validate(root):
    errors = []
    for path in (root / "configs").rglob("*.yaml"):
        try:
            if not isinstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict):
                errors.append(f"Not a mapping: {path}")
        except Exception as exc:
            errors.append(f"{path}: {exc}")
    manifest = json.loads((root / "references/source_manifest.json").read_text(encoding="utf-8"))
    for source in manifest["sources"]:
        if source.get("path") and source.get("sha256"):
            path = root / source["path"]
            if not path.is_file() or sha256(path) != source["sha256"]:
                errors.append(f"Source changed or missing: {source['source_id']}")
    project = tracking.read_project(root)
    stages = project["stages"]
    if project["focus_stage"] not in stages:
        errors.append("Invalid focus stage")
    records = {}
    for stage, meta in stages.items():
        if any(dep not in stages for dep in meta["depends_on"]):
            errors.append(f"Unknown dependency: {stage}")
        try:
            events = tracking.read_events(root, stage)
            current = tracking.current_records(events)
            for record in current.values():
                tracking.validate_record(root, stage, record)
                records[record["id"]] = record
        except (ValueError, KeyError) as exc:
            errors.append(f"{stage}: {exc}")
    for record in records.values():
        for linked in record.get("related_ids", []):
            if linked not in records:
                errors.append(f"Missing related record: {record['id']} -> {linked}")
    splits = yaml.safe_load((root / "configs/validation/splits.yaml").read_text(encoding="utf-8"))
    if set(splits["development"]) & set(splits["holdout_candidates"]):
        errors.append("Development and holdout candidates overlap")
    host_files = list((root / "configs/hosts").glob("*.yaml"))
    if len(host_files) != 11:
        errors.append("Expected 10 study hosts and the Moon method control")
    return {"status": "passed" if not errors else "failed", "errors": errors,
            "host_passports": len(host_files), "tracking_records": len(records),
            "limitations": "Structural checks only; draft physics and scientific gates are not certified."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Submoon research workspace")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("validate")
    commands.add_parser("smoke")
    track = commands.add_parser("track").add_subparsers(dest="track_command", required=True)
    track.add_parser("render")
    show = track.add_parser("show")
    show.add_argument("--stage", required=True)
    show.add_argument("--full", action="store_true")
    show.add_argument("--id")
    add = track.add_parser("add")
    add.add_argument("--stage", required=True)
    add.add_argument("--input", type=Path, required=True)
    add.add_argument("--actor", required=True)
    add.add_argument("--expected-revision", type=int, required=True)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "doctor":
            result = environment(check_imports=True)
            result["disk_free_gib"] = round(shutil.disk_usage(root).free / 2**30, 2)
            result["checked_utc"] = tracking.utc_now()
            result["network_checked"] = False
            write_json(root / "reports/benchmarks/environment.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(bool(result["failures"]))
        if args.command == "validate":
            result = validate(root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return int(bool(result["errors"]))
        if args.command == "smoke":
            folder, passed = run_smoke(root)
            print(json.dumps({"run_id": folder.name, "path": str(folder), "passed": passed}))
            return int(not passed)
        if args.track_command == "render":
            tracking.render(root)
            print("Tracking summaries rebuilt.")
        elif args.track_command == "show":
            events = tracking.read_events(root, args.stage)
            if args.id:
                print(json.dumps(tracking.current_records(events)[args.id], ensure_ascii=False, indent=2))
            elif args.full:
                print(json.dumps({"stage": args.stage, "revision": len(events),
                                  "records": list(tracking.current_records(events).values())}, ensure_ascii=False, indent=2))
            else:
                print((tracking.stage_dir(root, args.stage) / "SUMMARY.md").read_text(encoding="utf-8"))
        elif args.track_command == "add":
            record = json.loads(args.input.read_text(encoding="utf-8-sig"))
            event = tracking.add_record(root, args.stage, record, args.actor, args.expected_revision)
            print(json.dumps({"stage": args.stage, "revision": event["revision"], "id": record["id"]}))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1
