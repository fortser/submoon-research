"""Приёмка продуктов W0: SHA, повторяемость стартов, реальные области и регрессии."""

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
from submoon_research.execution import RunContext
from submoon_research.catalog.nominal_model import validate_model
from submoon_research.contracts.nominal_states import validate_nominal_states
from submoon_research.contracts.checkpoint import validate_checkpoint
from submoon_research.contracts.orbit import OrbitRecord
from submoon_research.contracts.baseline import BaselineConfig, validate_prerequisite
from submoon_research.sampling.design import (
    DomainSpec,
    domain_normalization,
    generate,
    reproduction_grid,
)
from submoon_research.workflows.smoke import write_json, sha256
from run_w0_baseline import physical_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-run", type=Path, required=True)
    parser.add_argument(
        "--checks-only",
        action="store_true",
        help="Проверить продукты без повторения уже выполненных регрессий",
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    with RunContext(
        root,
        "W0-completion",
        dict(
            experiment_id="W0-completion-verification-v1",
            data_kind="real_nominal_product_acceptance",
            baseline_run=args.baseline_run.as_posix(),
            checks_only=args.checks_only,
            sobol_power=6,
            seeds=[20261005, 20261006],
        ),
        wall_seconds=600,
        output_mib=64,
    ) as run:
        run.save_code(Path(__file__).resolve())
        manifest = run.ledger.json(args.baseline_run / "manifest.json", registered=True)
        if manifest["status"] != "completed" or manifest["validation"]["status"] != "passed":
            raise ValueError("Базовый ансамбль не принят")
        for path, digest in manifest["inputs_sha256"].items():
            run.ledger.bind(path, digest)
        for path, digest in manifest["artifacts_sha256"].items():
            run.ledger.bind(args.baseline_run / path, digest)
        run.ledger.verify_all()
        config = run.ledger.yaml(args.baseline_run / "config.resolved.yaml")
        BaselineConfig.model_validate(config)
        for name, path in config["prerequisites"].items():
            validate_prerequisite(name, run.ledger.json(path))
        model = validate_model(run.ledger.json(config["model_path"]))
        states = validate_nominal_states(run.ledger.json(config["states_path"]))
        records = run.ledger.json(args.baseline_run / "initial_conditions/baseline.json")["records"]
        outcomes = run.ledger.json(args.baseline_run / "results/outcomes.json")
        validation = run.ledger.json(args.baseline_run / "validation.json")
        domains = run.ledger.json(args.baseline_run / "results/domains.json")
        checks = dict(
            complete_36=len(records) == len(outcomes) == 36,
            unique_orbits=len({r["orbit_id"] for r in outcomes}) == 36,
            all_numeric_checks=bool(validation["checks"]) and all(validation["checks"].values()),
        )
        for product in outcomes:
            OrbitRecord.model_validate(product)
        for host, data in domains.items():
            _, _, gms, _, _, _, basis = physical_inputs(host, model, states)
            domain = DomainSpec.model_validate(data["domain"])
            grid = reproduction_grid(
                float(gms[0]),
                domain.inner_km,
                domain.hill_km,
                basis,
                lower_coefficient=1 + domain.contact_buffer,
                lower_formula_id="buffered_reference_contact_not_archive_roche",
            )
            checks[host + "_130_reproduced"] = grid == run.ledger.json(
                args.baseline_run / f"initial_conditions/{host}_own_130_grid.json"
            )
            for measure, normal in data["normalizations"].items():
                checks[host + "_" + measure + "_normalization"] = (
                    domain_normalization(domain, measure)[0] == normal
                )
                for realization, seed in enumerate([20261005, 20261006]):
                    sample = generate(
                        domain, measure, power=6, seed=seed, randomization=realization, basis=basis
                    )
                    repeat = generate(
                        domain, measure, power=6, seed=seed, randomization=realization, basis=basis
                    )
                    errors = []
                    for item in sample["records"]:
                        p, v = np.array(item["position"]), np.array(item["velocity"])
                        a, e = item["elements"]["a_km"], item["elements"]["e"]
                        energy = np.dot(v, v) / 2 - domain.gm_host / np.linalg.norm(p)
                        expected = -domain.gm_host / (2 * a)
                        errors.append(abs((energy - expected) / expected))
                        angular = np.linalg.norm(np.cross(p, v))
                        errors.append(abs(angular**2 / (domain.gm_host * a * (1 - e * e)) - 1))
                        assert a * (1 - e) >= domain.inner_km and a <= domain.hill_km
                    checks[f"{host}_{measure}_{realization}_reproducible"] = bool(
                        sample == repeat and max(errors) < 1e-12
                    )
                    write_json(
                        run.folder / f"initial_conditions/{host}_{measure}_{realization}.json",
                        sample,
                    )
        # Проверить метаданные всех сохранённых checkpoint окончательного ансамбля.
        for item in records:
            cp_path = args.baseline_run / ("checkpoints/" + item["orbit_id"] + ".json")
            if (root / cp_path).exists():
                _, _, gms, _, _, _, _ = physical_inputs(item["host"], model, states)
                options = dict(
                    rtol=config["rtol"],
                    atol_position=config["atol_position_km"],
                    atol_velocity=config["atol_velocity_km_s"],
                    max_step=item["period_seconds"] * config["max_step_period_fraction"],
                )
                validate_checkpoint(
                    run.ledger.json(cp_path),
                    initial=item["state"],
                    orbit_id=item["orbit_id"],
                    model_sha256=sha256(root / config["model_path"]),
                    gms=gms,
                    horizon=config["periods_per_orbit"] * item["period_seconds"],
                    options=options,
                )
        checks["all_checkpoint_metadata"] = True
        executions = []
        env = os.environ.copy()
        env.update(
            PYTHONIOENCODING="utf-8",
            OMP_NUM_THREADS="1",
            OPENBLAS_NUM_THREADS="1",
            MKL_NUM_THREADS="1",
        )
        if not args.checks_only:
            commands = [
                ("pytest", ["-m", "pytest", "-q"], 0),
                ("ruff", ["-m", "ruff", "check", "src", "scripts", "tests"], 0),
                ("validate", ["scripts/project.py", "validate"], 0),
                ("briefs", ["scripts/build_stage_briefs.py", "--check"], 0),
                ("smoke", ["scripts/project.py", "smoke"], 0),
                ("nominal", ["scripts/adopt_nominal_model.py"], 0),
                (
                    "alignment",
                    [
                        "scripts/acquire_planetary_alignment.py",
                        "--replay",
                        "runs/W0-alignment-20261005T105116Z-afdac2eb",
                    ],
                    0,
                ),
                ("l1_v03", ["scripts/verify_l1_specification.py"], 0),
                (
                    "baseline_audit",
                    ["scripts/audit_baseline.py", "--config", "configs/audits/W0_baseline_v3.yaml"],
                    1,
                ),
            ]
            for name, arguments, expected in commands:
                result = run.subprocess(
                    [sys.executable, "-X", "utf8", *arguments],
                    env=env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=180,
                )
                write_json(
                    run.folder / f"logs/{name}.json",
                    dict(
                        arguments=arguments,
                        exit_code=result.returncode,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    ),
                )
                executions.append(dict(name=name, exit_code=result.returncode, expected=expected))
                checks[name] = result.returncode == expected
                if name in {"nominal", "alignment"} and checks[name]:
                    new_run = root / "runs" / json.loads(result.stdout.strip())["run_id"]
                    if name == "nominal":
                        checks["model_reproduced"] = (
                            json.loads((new_run / "results/model.json").read_text(encoding="utf-8"))
                            == model
                        )
                    else:
                        new_states = json.loads(
                            (new_run / "results/aligned_states.json").read_text(encoding="utf-8")
                        )
                        checks["aligned_states_reproduced"] = all(
                            new_states[k] == states[k]
                            for k in ["epoch_jd_tdb", "barycentric", "host_relative", "sources"]
                        )
                print(name, "passed" if checks[name] else "failed", flush=True)
        write_json(run.folder / "results/executions.json", executions)
        acceptance = run.validate(
            checks,
            scope="W0_nominal_completion_not_W1_freeze_or_W2_production",
            limitations=[
                "Unknown covariances remain null; operational spheres only",
                "Different short horizons; no population, host ranking or long-term survival claim",
            ],
        )
    print(json.dumps(dict(run_id=run.run_id, status=acceptance["status"])))
    return int(acceptance["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
