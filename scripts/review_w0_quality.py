"""Повторяемый локальный аудит W0; исходники и старые runs не изменяются."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from submoon_research.tracking import utc_now
from submoon_research.workflows.l1_specification import verify_l1_contract
from submoon_research.workflows.smoke import environment, sha256, write_json

ROOT = Path(__file__).resolve().parents[1]
HISTORY_CUTOFF = "20261004T125700Z"  # Фиксированный срез до первой попытки этого аудита.


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def hash_check(path, expected):
    return path.is_file() and sha256(path) == expected


def inspect_provenance(manifests):
    records = []
    for path in manifests:
        data = read_json(path)
        folder = path.parent
        artifacts = data.get("artifacts_sha256", {})
        bad_artifacts = [key for key, digest in artifacts.items()
                         if not hash_check(folder / key, digest)]
        missing_original_inputs, changed_inputs, unrecoverable_inputs = [], [], []
        for key, digest in data.get("inputs_sha256", {}).items():
            current = ROOT / key
            if not current.is_file():
                missing_original_inputs.append(key)
            elif not hash_check(current, digest):
                changed_inputs.append(key)
            if not hash_check(current, digest) and not hash_check(folder / "inputs" / key, digest):
                unrecoverable_inputs.append(key)
        code_not_recoverable = [key for key, digest in (data.get("code_sha256") or {}).items()
                                if not hash_check(folder / "code" / key, digest)
                                and not hash_check(ROOT / key, digest)]
        unlisted = [p.relative_to(folder).as_posix() for p in folder.rglob("*")
                    if p.is_file() and p != path and p.relative_to(folder).as_posix() not in artifacts]
        validation = (read_json(folder / "validation.json") if (folder / "validation.json").is_file()
                      else dict(status="missing"))
        records.append(dict(run_id=data["run_id"], status=data["status"],
            validation=validation["status"], artifacts_count=len(artifacts),
            code_hashes_recorded=bool(data.get("code_sha256")),
            bad_artifacts=bad_artifacts, unlisted_artifacts=unlisted,
            missing_original_inputs=missing_original_inputs, changed_current_inputs=changed_inputs,
            unrecoverable_inputs=unrecoverable_inputs, code_not_recoverable=code_not_recoverable,
            failed_checks=[key for key, value in validation.get("checks", {}).items() if not value]))
    source_checks = []
    for manifest in sorted((ROOT / "references/manifests").glob("*.json")):
        data = read_json(manifest)
        for item in data.get("files", []):
            if "path" in item and "sha256" in item:
                source_checks.append(dict(manifest=manifest.relative_to(ROOT).as_posix(),
                    path=item["path"], ok=hash_check(ROOT / item["path"], item["sha256"])))
    claims = list(csv.DictReader((ROOT / "results/evidence/claims.csv").open(encoding="utf-8")))
    source_ids = {row["source_id"] for row in read_json(ROOT / "references/source_manifest.json")["sources"]}
    latest = {}
    for line in (ROOT / "tracking/stages/W0/journal.jsonl").read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        record = event.get("record", event)
        latest[record["id"]] = record
    claim_links = []
    for row in claims:
        claim_links.append(dict(claim_id=row["claim_id"],
            missing_runs=[r for r in row["run_ids"].split(";") if not (ROOT / "runs" / r / "manifest.json").is_file()],
            missing_sources=[s for s in row["source_ids"].split(";") if s not in source_ids],
            missing_checks=[c for c in row["check_ids"].split(";") if c not in latest]))
    return dict(runs=records, source_hash_checks=source_checks, claim_links=claim_links)


def l1_mutations():
    spec = yaml.safe_load((ROOT / "configs/experiments/L1_baseline_spec_v0_2.yaml").read_text(encoding="utf-8"))
    route = spec["routes"]["independent_reproduction"]
    states = read_json(ROOT / route["states"]["path"])
    passports = {host: yaml.safe_load((ROOT / item["passport"]).read_text(encoding="utf-8"))
                 for host, item in route["hosts"].items()}
    old = (ROOT / "data/raw/W0-himalia-sources-20261004/jup344.cmt").read_text(encoding="utf-8")
    new = (ROOT / "data/raw/W0-l1-sources-20261004/jup347.cmt").read_text(encoding="utf-8")
    mutations = [
        ("rotating_frame", ["frame", "rotating_frame"], True),
        ("wrong_output_origin", ["frame", "output_origin"], "jupiter"),
        ("epoch_index_conflict", ["epoch", "initial_epoch_index"], 1),
        ("tides_without_parameters", ["physics", "tides"], True),
        ("disable_monopoles", ["physics", "pairwise_monopoles"], False),
        ("remove_iapetus_host_and_parent", ["massive_body_sets", "iapetus"], ["sun"]),
        ("remove_external_parameters", ["external_parameters"], {}),
        ("wrong_energy_formula", ["events", "article_energy_crossing", "formula"], "specific_E2=v_rel^2/2+GM_host/r"),
        ("negative_contact_radius", ["events", "reference_contact", "submoon_radius_km"], -0.1),
        ("grid_denominator", ["initial_measure", "count_per_host"], 1),
    ]
    results = []
    for name, keys, value in mutations:
        trial = copy.deepcopy(spec)
        target = trial["routes"]["independent_reproduction"]
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        checks, _ = verify_l1_contract(trial, states, passports, old, new)
        results.append(dict(probe=name, path="routes.independent_reproduction." + ".".join(keys),
            injected_value=value, rejected=not all(checks.values()),
            failed_checks=[key for key, ok in checks.items() if not ok], checks_count=len(checks),
            data_kind="synthetic_invalid_configuration_probe"))
    return spec, results


def main():
    started = utc_now()
    start = time.perf_counter()
    resources = yaml.safe_load((ROOT / "configs/resources.yaml").read_text(encoding="utf-8"))
    free = shutil.disk_usage(ROOT).free
    if free < resources["minimum_free_disk_gib"] * 2**30:
        raise RuntimeError("Недостаточный резерв диска")
    historical = sorted((ROOT / "runs").glob("W0-*/manifest.json"))
    historical = [p for p in historical if p.parent.name.rsplit("-", 2)[-2] < HISTORY_CUTOFF]
    run_id = "W0-quality-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    folder = ROOT / "runs" / run_id
    folder.mkdir(exist_ok=False)
    code_paths = [Path(__file__).resolve()] + list((ROOT / "src/submoon_research").rglob("*.py"))
    code = {p.relative_to(ROOT).as_posix(): sha256(p) for p in code_paths}
    for path in code_paths:
        dest = folder / "code" / path.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
    git = subprocess.run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True, check=True)
    (folder / "git_status_before.txt").write_text(git.stdout, encoding="utf-8")
    provenance = inspect_provenance(historical)
    write_json(folder / "results/provenance.json", provenance)
    spec, mutations = l1_mutations()
    write_json(folder / "results/l1_mutations.json", mutations)
    # Отдельный отрицательный пример: канонический конфиг не изменяется.
    malformed = copy.deepcopy(spec)
    malformed["routes"]["independent_reproduction"]["physics"]["tides"] = True
    malformed["routes"]["independent_reproduction"]["frame"]["rotating_frame"] = True
    malformed["routes"]["independent_reproduction"]["massive_body_sets"]["iapetus"] = ["sun"]
    mutation_path = folder / "inputs/synthetic_invalid_L1.yaml"
    mutation_path.parent.mkdir(parents=True, exist_ok=True)
    mutation_path.write_text(yaml.safe_dump(malformed, allow_unicode=True, sort_keys=False), encoding="utf-8")
    commands = [
        ("pytest", ["-m", "pytest"], 0),
        ("ruff", ["-m", "ruff", "check", "src", "scripts", "tests"], 0),
        ("validate", ["scripts/project.py", "validate"], 0),
        ("stage_briefs", ["scripts/build_stage_briefs.py", "--check"], 0),
        ("baseline", ["scripts/audit_baseline.py", "--config", "configs/audits/W0_baseline_v3.yaml"], 1),
        ("iapetus", ["scripts/verify_iapetus_passport.py", "--config", "configs/audits/Iapetus_passport_v1.yaml"], 0),
        ("himalia", ["scripts/verify_himalia_passport.py"], 0),
        ("ganymede", ["scripts/verify_ganymede_passport.py"], 0),
        ("states_replay", ["scripts/acquire_geometric_states.py", "--replay", "runs/W0-states-20261004T104417Z-eacbd97e"], 0),
        ("l1", ["scripts/verify_l1_specification.py"], 0),
        ("l1_invalid_probe", ["scripts/verify_l1_specification.py", "--config", mutation_path.relative_to(ROOT).as_posix()], 1),
        ("smoke", ["scripts/project.py", "smoke"], 0),
    ]
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    executions = []
    for name, args, expected in commands:
        before = {p.name for p in (ROOT / "runs").iterdir() if p.is_dir()}
        wall = time.perf_counter()
        result = subprocess.run([sys.executable, "-X", "utf8", *args], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=120, check=False)
        elapsed = time.perf_counter() - wall
        log = folder / "logs" / (name + ".txt")
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout + "\nSTDERR:\n" + result.stderr, encoding="utf-8")
        new_runs = sorted(p.name for p in (ROOT / "runs").iterdir() if p.is_dir() and p.name not in before)
        entry = dict(name=name, args=args, exit_code=result.returncode, expected_exit_code=expected,
            met_expectation=result.returncode == expected, wall_seconds=elapsed, run_ids=new_runs,
            log=log.relative_to(folder).as_posix())
        executions.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    write_json(folder / "results/executions.json", executions)
    replay_id = next(e["run_ids"][0] for e in executions if e["name"] == "states_replay")
    old_states = read_json(ROOT / "data/processed/W0-geometric-states-v1/geometric_states.json")
    new_states = read_json(ROOT / "runs" / replay_id / "results/geometric_states.json")
    equality = {key: old_states[key] == new_states[key] for key in
                ("epoch_jd_tdb", "barycentric", "host_relative", "sources")}
    write_json(folder / "results/replay_equality.json", equality)
    new_manifests = [ROOT / "runs" / r / "manifest.json" for e in executions
                     if e["name"] != "smoke" for r in e["run_ids"]]
    write_json(folder / "results/reproduced_runs_integrity.json", inspect_provenance(new_manifests))
    validation = dict(status="failed", audit_execution="completed_with_findings",
        scope="W0_quality_review_no_scientific_gate_release",
        routine_commands_ok=all(e["met_expectation"] for e in executions if e["name"] != "l1_invalid_probe"),
        invalid_config_cli_rejected=next(e["met_expectation"] for e in executions if e["name"] == "l1_invalid_probe"),
        unhandled_l1_mutations=[m["probe"] for m in mutations if not m["rejected"]],
        replay_exact=all(equality.values()), historical_runs=len(historical),
        bad_historical_artifacts=sum(len(r["bad_artifacts"]) for r in provenance["runs"]),
        bad_source_hashes=sum(not item["ok"] for item in provenance["source_hash_checks"]))
    write_json(folder / "validation.json", validation)
    config = dict(experiment_id="W0-quality-review-v1", synthetic_probes=True,
        scientific_inputs="existing_local_W0_only", network_calls=0, paid_calls=0,
        concurrency=1, per_command_timeout_seconds=120, original_run_ids=[p.parent.name for p in historical])
    (folder / "config.resolved.yaml").write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    inputs = {p.relative_to(ROOT).as_posix(): sha256(p) for p in historical}
    env_info = environment()
    scientific = dict(config=config, inputs=inputs, code=code, environment=env_info)
    write_json(folder / "manifest.json", dict(schema_version="0.1", run_id=run_id,
        experiment_id=config["experiment_id"], started_utc=started, finished_utc=utc_now(),
        status="completed", validation={"status": "failed"}, data_kind="implementation_quality_audit",
        scientific_id=hashlib.sha256(json.dumps(scientific, sort_keys=True).encode()).hexdigest(),
        inputs_sha256=inputs, code_sha256=code, environment=env_info,
        resources=dict(workers=1, network_calls=0, paid_calls=0, free_disk_before_bytes=free),
        wall_seconds=time.perf_counter() - start,
        artifacts_sha256={p.relative_to(folder).as_posix(): sha256(p) for p in folder.rglob("*") if p.is_file()}))
    print(json.dumps(dict(run_id=run_id, validation=validation), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
