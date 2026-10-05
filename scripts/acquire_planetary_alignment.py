"""Два геометрических запроса AstroBridge и измерение DE441/DE442 на общей эпохе."""

import json
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import spiceypy as spice
import yaml
from submoon_research.execution import RunContext
from submoon_research.acquisition.geometric_states import build_requests, parse_vectors
from submoon_research.workflows.smoke import write_json, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/acquisition/W0_alignment_v2.yaml")
    )
    parser.add_argument("--replay", type=Path)
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Создать новую принятую версию; существующую не перезаписывать",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, "W0-alignment", wall_seconds=180, output_mib=64)
    with run:
        run.save_code(Path(__file__).resolve())
        config = run.ledger.yaml(args.config, registered=True)
        run.save_config(config)
        for path, digest in config["inputs_sha256"].items():
            run.ledger.bind(path, digest)
        original = run.ledger.json(config["states_path"])
        kernel = config["de442_kernel"]
        run.ledger.read(kernel)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        old = None
        if args.replay:
            source = (root / args.replay).resolve()
            if not source.is_relative_to(root / "runs"):
                raise ValueError("Replay должен быть внутри runs")
            old = run.ledger.json(
                (source / "manifest.json").relative_to(root).as_posix(), registered=True
            )
            if old["status"] != "completed" or old["validation"]["status"] != "passed":
                raise ValueError("Replay требует завершённый проверенный run")
            for path, digest in old["artifacts_sha256"].items():
                run.ledger.bind((source / path).relative_to(root).as_posix(), digest)
        else:
            paths = yaml.safe_load((root / "configs/paths.local.yaml").read_text(encoding="utf-8"))
            python = Path(paths["astrobridge_python"])
            bridge = Path(paths["astrobridge_root"])
            workspace = (root / paths["astrobridge_workspace"]).resolve()
            if not python.is_file() or not bridge.is_dir() or not workspace.is_relative_to(root):
                raise ValueError("Нет проверенного AstroBridge/Python/workspace")
            settings = run.folder / "astrobridge.config.json"
            write_json(
                settings,
                dict(
                    workspace=str(workspace),
                    proxy_mode="environment",
                    timeout=30,
                    retries=0,
                    max_response_mb=1,
                    max_download_mb=1,
                ),
            )
            base = [
                str(python),
                "-X",
                "utf8",
                "-m",
                "astrobridge",
                "--config",
                str(settings),
                "--workspace",
                str(workspace),
                "--quiet",
            ]
            run.manifest["astrobridge_code_sha256"] = {
                p.relative_to(bridge).as_posix(): sha256(p)
                for p in (bridge / "src/astrobridge").rglob("*.py")
            }
            doctor = run.subprocess(
                base + ["doctor"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=35,
            )
            write_json(run.folder / "environment/bridge_doctor.json", json.loads(doctor.stdout))
            if doctor.returncode or json.loads(doctor.stdout)["status"] != "success":
                raise ValueError("Doctor AstroBridge failed")
        jobs = build_requests(config)
        parsed = {}
        for job in jobs:
            request = job["request"]
            name = job["name"]
            write_json(run.folder / "results" / f"{name}.request.json", request)
            if old:
                response = run.ledger.json(
                    (source / "results" / f"{name}.response.json").relative_to(root).as_posix()
                )
                data = run.ledger.read(
                    (source / "results" / f"{name}.raw.txt").relative_to(root).as_posix()
                )
            else:
                process = run.subprocess(
                    base + ["run", "-", "--preview", "2", "--no-cache"],
                    input=json.dumps(request),
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=45,
                )
                response = json.loads(process.stdout)
                if process.returncode or response.get("status") != "success":
                    raise ValueError("Planetary request failed: " + name)
                if not response.get("directory"):
                    raise ValueError("Нет исходного текста в ответе AstroBridge")
                directory = Path(response["directory"]).resolve()
                if directory.parent != workspace / "runs":
                    raise ValueError("Артефакт вне workspace")
                for artifact in response["artifacts"]:
                    if sha256(directory / artifact["name"]) != artifact["sha256"]:
                        raise ValueError("SHA артефакта AstroBridge не совпадает")
                data = (directory / "horizons.txt").read_bytes()
            write_json(run.folder / "results" / f"{name}.response.json", response)
            target = run.folder / "results" / f"{name}.raw.txt"
            target.write_bytes(data)
            parsed[name] = parse_vectors(data.decode("utf-8"), response, request, config)
            if not old:
                run.manifest["resources"]["network_calls"] += len(response.get("http", []))
        # Временной контракт SPICE: ET=секунды TDB от J2000, NONE, J2000/ICRF.
        differences = {}
        try:
            spice.kclear()
            spice.furnsh(str(root / kernel))
            for name, identifier in [("jupiter_barycenter", "5"), ("saturn_barycenter", "6")]:
                # JD binary64 теряет десятки микросекунд при вычитании эпохи.
                # Источник задан календарём TDB и точным шагом 60 s, парсер проверил оба момента.
                seconds = [
                    (config["epoch_jd_tdb"] - 2451545.0) * 86400 + i * config["cadence_seconds"]
                    for i in range(2)
                ]
                de442 = np.array(
                    [spice.spkezr(identifier, float(et), "J2000", "NONE", "0")[0] for et in seconds]
                )
                de441 = np.array(parsed[name]["states_km_km_s"])
                if parsed[name]["source"]["target"]["ephemeris_source"] != "DE441":
                    raise ValueError("Не подтверждён DE441 для barycenter")
                differences[name] = dict(
                    de441=de441.tolist(),
                    de442=de442.tolist(),
                    delta_de442_minus_de441=(de442 - de441).tolist(),
                    position_km=float(np.linalg.norm(de442[0, :3] - de441[0, :3])),
                    velocity_km_s=float(np.linalg.norm(de442[0, 3:] - de441[0, 3:])),
                )
        finally:
            spice.kclear()
        shifted = json.loads(json.dumps(original))
        delta = np.array(differences["jupiter_barycenter"]["delta_de442_minus_de441"])
        shifted["barycentric"]["himalia"] = (
            np.array(original["barycentric"]["himalia"]) - delta
        ).tolist()
        for host, bodies in shifted["host_relative"].items():
            for body in bodies:
                shifted["host_relative"][host][body] = (
                    np.array(shifted["barycentric"][body]) - np.array(shifted["barycentric"][host])
                ).tolist()
        shifted["experiment_id"] = "W0-geometric-states-aligned-v3"
        shifted["supersedes"] = "W0-geometric-states-aligned-v2"
        shifted["alignment"] = dict(
            method="translate_Himalia_DE442_Jupiter_barycenter_to_DE441_at_each_epoch",
            changed_body="himalia",
            source_run_id=run.run_id,
            original_preserved=True,
            not_joint_ephemeris_fit=True,
        )
        checks = {
            "finite_alignment": bool(np.isfinite(delta).all()),
            "two_epochs": delta.shape == (2, 6),
            "himalia_only": all(
                shifted["barycentric"][name] == original["barycentric"][name]
                for name in original["barycentric"]
                if name != "himalia"
            ),
        }
        validation = run.validate(
            checks, scope="DE441_DE442_initial_state_alignment_not_long_term_ephemeris_fit"
        )
        write_json(run.folder / "results/differences.json", differences)
        write_json(run.folder / "results/aligned_states.json", shifted)
        if validation["status"] == "passed" and args.publish:
            target = root / "data/processed/W0-geometric-states-aligned-v3/geometric_states.json"
            if target.exists():
                raise FileExistsError("Не перезаписывать состояния")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((run.folder / "results/aligned_states.json").read_bytes())
            write_json(
                root / "references/manifests/aligned_states_v3.json",
                dict(
                    schema_version="0.1",
                    run_id=run.run_id,
                    files=[dict(path=target.relative_to(root).as_posix(), sha256=sha256(target))]
                    + [dict(path=path, sha256=digest) for path, digest in run.ledger.used.items()],
                ),
            )
    print(json.dumps(dict(run_id=run.run_id, status=validation["status"], differences=differences)))
    return int(validation["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
