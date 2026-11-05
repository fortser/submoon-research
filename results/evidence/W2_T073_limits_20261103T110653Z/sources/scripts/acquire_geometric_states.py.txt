"""Малые геометрические состояния через внешний AstroBridge; offline replay без сети."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from submoon_research.acquisition.geometric_states import (
    assemble_states,
    build_requests,
    parse_vectors,
)
from submoon_research.workflows.smoke import sha256, write_json
from submoon_research.execution import RunContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/acquisition/W0_geometric_states_v1.yaml"))
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--acquire", action="store_true")
    mode.add_argument("--replay", type=Path, help="Завершённый исходный run с snapshot ответов")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run=RunContext(root,'W0-states',wall_seconds=600,output_mib=64)
    try:
        with run:
            run.save_code(Path(__file__).resolve())
            config=run.ledger.yaml(args.config,registered=True)
            run.save_config(config)
            limits=config['limits']
            run.wall_seconds=min(limits['wall_seconds'],run.wall_seconds)
            run.output_bytes=min(limits['output_mib']*2**20,run.output_bytes)
            validation=execute(run,args,config)
        print(json.dumps(dict(run_id=run.run_id,status=run.manifest['status'],validation=validation['status'],metrics=validation.get('metrics')),ensure_ascii=False))
        return int(validation['status']!='passed')
    except (ValueError,RuntimeError,OSError,KeyError,TypeError,TimeoutError) as exc:
        print(json.dumps(dict(run_id=run.run_id,status=run.manifest['status'],error=str(exc)),ensure_ascii=False))
        return 1


def execute(run,args,config):
    root,folder,ledger=run.root,run.folder,run.ledger
    manifest,run_id=run.manifest,run.run_id
    start=run.started_wall
    limits=config['limits']
    jobs=build_requests(config)
    if len(jobs)!=limits['queries']:
        raise ValueError('Invalid request count')
    ledger.read(config['au_source_path'],registered=True)
    write_json(folder/'requests.json',jobs)
    limitations=['Геометрический снимок; не приёмка силы/событий/интегратора.',
        'Смешение эфемеридных решений сохранено; ковариации не получены.']
    env=os.environ.copy()
    env.update(PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
    manifest['resources']['http_calls']=0
    def budget():
        run.check_budget()
        return run.wall_seconds-(time.perf_counter()-start)
    if config["au_km"] != 149597870.7 or "149597870700" not in ledger.text(config["au_source_path"]):
        raise ValueError("Нет подтверждения точной единицы AU")
    if config["day_seconds"] != 86400:
        raise ValueError("Неверная единица суток")
    if args.acquire:
        paths = yaml.safe_load((root / "configs/paths.local.yaml").read_text(encoding="utf-8"))
        bridge_root, python = Path(paths["astrobridge_root"]), Path(paths["astrobridge_python"])
        workspace = (root / paths["astrobridge_workspace"]).resolve()
        if not bridge_root.is_dir() or not python.is_file() or not workspace.is_relative_to(root):
            raise ValueError("AstroBridge/Python/workspace недоступны или вне проекта")
        settings_path = folder / "astrobridge.config.json"
        write_json(settings_path, dict(workspace=str(workspace), proxy_mode="environment",
            timeout=limits["request_timeout_seconds"], retries=limits["retries"],
            max_response_mb=limits["response_mib"], max_download_mb=limits["response_mib"]))
        base = [str(python), "-X", "utf8", "-m", "astrobridge", "--config", str(settings_path),
                "--workspace", str(workspace), "--quiet"]

        def invoke(arguments, input_text=None):
            process = run.subprocess(base + arguments, env=env, input=input_text,
                capture_output=True, text=True, encoding="utf-8", check=False,
                timeout=min(limits["subprocess_timeout_seconds"], budget()))
            return process, json.loads(process.stdout)

        for command in ("doctor", "schema", "providers"):
            process, response = invoke([command])
            write_json(folder / f"environment/astrobridge_{command}.json", response)
            if process.returncode:
                raise ValueError(f"AstroBridge {command} недоступен")
        doctor = json.loads((folder / "environment/astrobridge_doctor.json").read_text(encoding="utf-8"))
        if doctor["status"] != "success" or Path(doctor["workspace"]).resolve() != workspace:
            raise ValueError("Doctor не подтверждает workspace")
        probe = run.subprocess([str(python), "-X", "utf8", "-c",
            "import json,sys,platform,importlib.metadata as m; print(json.dumps(dict(python=sys.version,platform=platform.platform(),packages={k:m.version(k) for k in ['astrobridge','astropy','requests','jsonschema']})))"],
            env=env, text=True, encoding="utf-8", capture_output=True, check=True, timeout=10)
        manifest["astrobridge_environment"] = json.loads(probe.stdout)
        manifest["astrobridge_code_sha256"] = {p.relative_to(bridge_root).as_posix(): sha256(p)
            for p in sorted((bridge_root / "src/astrobridge").rglob("*.py"))}
        schema_check = run.subprocess([str(python), "-X", "utf8", "-c",
            "import json,sys; from jsonschema import Draft202012Validator; d=json.load(sys.stdin); v=Draft202012Validator(d['schema']); [v.validate(j['request']) for j in d['jobs']]; print(json.dumps(dict(status='passed',queries=len(d['jobs']))))"],
            input=json.dumps(dict(jobs=jobs, schema=json.loads((folder / "environment/astrobridge_schema.json").read_text(encoding="utf-8")))),
            env=env, text=True, encoding="utf-8", capture_output=True,
            check=True, timeout=10)
        write_json(folder / "environment/request_schema_check.json", json.loads(schema_check.stdout))
        for job in jobs:
            query_dir = folder / "acquisition" / job["name"]
            write_json(query_dir / "request.json", job["request"])
            process, response = invoke(["run", "-", "--preview", str(config["rows_per_query"]), "--no-cache"],
                                       json.dumps(job["request"]))
            write_json(query_dir / "response.json", response)
            # Проверять каталог даже при ошибке; сохранять evidence неуспешного запроса.
            if response.get("directory"):
                source = Path(response["directory"]).resolve()
                if source.parent != workspace / "runs":
                    raise ValueError("Некорректный каталог ответа AstroBridge")
                for path in source.iterdir():
                    if path.is_file():
                        target = query_dir / "astrobridge" / path.name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(path.read_bytes())
                manifest["resources"]["http_calls"] += len(response.get("http", []))
            if process.returncode or response.get("status") != "success":
                raise RuntimeError(f"AstroBridge {job['name']}: {response.get('error')}")
            print(json.dumps(dict(query=job["name"], astrobridge_run_id=response["run_id"], rows=response["row_count"])), flush=True)
            budget()
    else:
        source_run = (root / args.replay).resolve()
        if not source_run.is_relative_to(root / "runs") or source_run == folder:
            raise ValueError("Replay требует отдельный run проекта")
        old = ledger.json((source_run / "manifest.json").relative_to(root).as_posix(), registered=True)
        for path, digest in old['artifacts_sha256'].items():
            name=(source_run/path).relative_to(root).as_posix()
            ledger.bind(name,digest)
            ledger.read(name)
        if old["status"] != "completed" or old["validation"]["status"] != "passed":
            raise ValueError("Replay требует проверенный завершённый run")
        original_config = ledger.yaml((source_run / "config.resolved.yaml").relative_to(root).as_posix())
        if config != original_config:
            raise ValueError("Replay не допускает смену постановки")
        manifest["source_run_id"] = old["run_id"]
        for subdir in ('acquisition','environment'):
            for source in (source_run/subdir).rglob('*'):
                if source.is_file():
                    target=folder/source.relative_to(source_run)
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_bytes(ledger.read(source.relative_to(root).as_posix()))
    parsed = {}
    for job in jobs:
        query_dir = folder / "acquisition" / job["name"]
        response = json.loads((query_dir / "response.json").read_text(encoding="utf-8"))
        for artifact in response["artifacts"]:
            if sha256(query_dir / "astrobridge" / artifact["name"]) != artifact["sha256"]:
                raise ValueError("Повреждён артефакт AstroBridge")
        parsed[job["name"]] = parse_vectors(
            (query_dir / "astrobridge/horizons.txt").read_text(encoding="utf-8"), response, job["request"], config)
    states, checks, metrics = assemble_states(parsed, config)
    checks.update(source_artifacts_sha256=True, headers_units_epochs_finite=True,
        au_exact_source=True, thirteen_queries=True,
        wall_limit=budget() > 0, output_limit=True)
    validation = dict(status="passed" if all(checks.values()) else "failed", checks=checks,
        metrics=metrics, tolerances=config["validation_tolerances"], limitations=limitations,
        parser_metrics={key: {k: v for k, v in item.items() if k.endswith("error")}
                        for key, item in parsed.items()})
    states["run_id"] = run_id
    states["source_run_id"] = manifest.get("source_run_id", run_id)
    states["validation_status"] = validation["status"]
    write_json(folder / "results/geometric_states.json", states)
    run.manifest['validation'] = dict(status=validation['status'],scope='geometric_snapshot_replay_only')
    write_json(folder / 'validation.json',validation)
    return validation


if __name__=='__main__':
    raise SystemExit(main())
