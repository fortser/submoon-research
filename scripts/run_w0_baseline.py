"""Малый ансамбль трёх реальных хозяев с конвергенцией, событиями и restart."""

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
import rebound
from submoon_research.execution import RunContext
from submoon_research.contracts.baseline import BaselineConfig, validate_prerequisite
from submoon_research.contracts.nominal_states import validate_nominal_states
from submoon_research.contracts.checkpoint import validate_checkpoint
from submoon_research.contracts.orbit import OrbitRecord
from submoon_research.catalog.nominal_model import validate_model
from submoon_research.dynamics.baseline import integrate
from submoon_research.sampling.design import (
    DomainSpec,
    domain_normalization,
    elements_to_cartesian,
    reproduction_grid,
)
from submoon_research.workflows.smoke import write_json, sha256

from submoon_research.dynamics.initialization import physical_inputs, PARENTS


def ias15_reference(initial, gms, figures, times, epsilon, budget):
    sim = rebound.Simulation()
    sim.G = 1.0
    states = np.asarray(initial).reshape(-1, 6)
    for index, row in enumerate(states):
        sim.add(
            m=float(gms[index]) if index < len(gms) else 0.0,
            x=row[0],
            y=row[1],
            z=row[2],
            vx=row[3],
            vy=row[4],
            vz=row[5],
        )
    sim.integrator = "ias15"
    sim.integrator.epsilon = epsilon
    sim.integrator.adaptive_mode = "PRS23"
    sim.dt = max(float(times[-1]) / 10000, 1e-6)

    def extra(pointer):
        particles = pointer.contents.particles
        for figure in figures:
            planet = particles[figure["index"]]
            pole = np.array(figure["pole"])
            gm = gms[figure["index"]]
            for j in range(len(states)):
                if j == figure["index"]:
                    continue
                other = particles[j]
                displacement = np.array(
                    [other.x - planet.x, other.y - planet.y, other.z - planet.z]
                )
                distance = np.linalg.norm(displacement)
                projection = np.dot(displacement, pole)
                factor = 1.5 * gm * figure["j2"] * figure["radius"] ** 2 / distance**5
                force = factor * (
                    (5 * (projection / distance) ** 2 - 1) * displacement - 2 * projection * pole
                )
                other.ax += force[0]
                other.ay += force[1]
                other.az += force[2]
                ratio = gms[j] / gm if j < len(gms) else 0.0
                planet.ax -= ratio * force[0]
                planet.ay -= ratio * force[1]
                planet.az -= ratio * force[2]

    sim.additional_forces = extra
    sim.force_is_velocity_dependent = 0
    output = []
    for t in times:
        budget()
        sim.integrate(float(t))
        host, probe = sim.particles[0], sim.particles[-1]
        output.append(
            [
                probe.x - host.x,
                probe.y - host.y,
                probe.z - host.z,
                probe.vx - host.vx,
                probe.vy - host.vy,
                probe.vz - host.vz,
            ]
        )
    return np.asarray(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/experiments/W0_nominal_baseline_v1.yaml")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, "W0-baseline", wall_seconds=600, output_mib=128)
    with run:
        run.save_code(Path(__file__).resolve())
        config = BaselineConfig.model_validate(
            run.ledger.yaml(args.config, registered=True)
        ).model_dump()
        run.save_config(config)
        run.wall_seconds = config["wall_seconds"]
        run.output_bytes = config["output_mib"] * 2**20
        for path, digest in config["inputs_sha256"].items():
            run.ledger.bind(path, digest)
        for path in (config["model_manifest"], config["states_manifest"]):
            run.ledger.bind_manifest(run.ledger.json(path))
        run.ledger.verify_all()
        model = validate_model(run.ledger.json(config["model_path"]))
        states = validate_nominal_states(run.ledger.json(config["states_path"]))
        original = validate_nominal_states(run.ledger.json(config["original_states_path"]))
        if (
            states["time_scale"] != "TDB"
            or states["frame"] != "ICRF"
            or states["position_unit"] != "km"
            or states["velocity_unit"] != "km/s"
            or states["geometric_corrections"] != "NONE"
        ):
            raise ValueError("Не приняты единицы/оси геометрии")
        for name, path in config["prerequisites"].items():
            validate_prerequisite(name, run.ledger.json(path))
        setup = {}
        planned = []
        domains = {}
        for host in config["hosts"]:
            names, relative, gms, figures, radii, a_host, basis = physical_inputs(
                host, model, states
            )
            domain = DomainSpec(
                domain_id=host + "-nominal-v1",
                gm_host=float(gms[0]),
                gm_parent=model["bodies"][PARENTS[host]]["gm"]["value"],
                a_host_km=a_host,
                reference_radius_km=radii[0] - model["submoon_radius_km"],
                submoon_radius_km=model["submoon_radius_km"],
                eccentricity_max=0.3,
                contact_buffer=config["contact_buffer"],
            )
            norms = {
                measure: domain_normalization(domain, measure)[0]
                for measure in ("uniform_a_e_cos_i_phases", "uniform_log_a", "canonical_volume")
            }
            domains[host] = dict(
                domain=domain.model_dump(),
                hill_km=domain.hill_km,
                normalizations=norms,
                basis_icrf=basis.tolist(),
                excluded_contact_buffer_fraction_geometric=1
                - norms["uniform_a_e_cos_i_phases"]
                / domain_normalization(
                    domain.model_copy(update={"contact_buffer": 0.0}), "uniform_a_e_cos_i_phases"
                )[0],
            )
            grid = reproduction_grid(
                float(gms[0]),
                domain.inner_km,
                domain.hill_km,
                basis,
                lower_coefficient=1 + config["contact_buffer"],
                lower_formula_id="buffered_reference_contact_not_archive_roche",
            )
            write_json(run.folder / "initial_conditions" / f"{host}_own_130_grid.json", grid)
            setup[host] = (names, relative, gms, figures, radii)
            for a in np.geomspace(
                domain.inner_km, domain.hill_km * config["outer_hill_fraction"], config["a_count"]
            ):
                for inclination in config["inclination_degrees"]:
                    probe = elements_to_cartesian(
                        float(gms[0]),
                        float(a),
                        0.0,
                        math.cos(math.radians(inclination)),
                        0.0,
                        0.0,
                        0.0,
                        basis,
                    )
                    planned.append(
                        dict(
                            orbit_id=f"{host}-{len(planned):03}",
                            host=host,
                            a_km=float(a),
                            inclination_degrees=inclination,
                            state=np.vstack([relative, probe]).ravel().tolist(),
                            period_seconds=2 * math.pi * math.sqrt(a**3 / gms[0]),
                        )
                    )
        selected = (
            planned
            if config["orbit_indices"] is None
            else [planned[i] for i in config["orbit_indices"]]
        )
        write_json(
            run.folder / "initial_conditions/baseline.json",
            dict(
                epoch_jd_tdb=2451545.0,
                units="km,km/s,s",
                planned_count=len(planned),
                selected_count=len(selected),
                records=selected,
            ),
        )
        write_json(run.folder / "results/domains.json", domains)
        checks = {}
        outcomes = []
        costs = []
        for index, item in enumerate(selected):
            run.check_budget()
            host = item["host"]
            names, relative, gms, figures, radii = setup[host]
            period = item["period_seconds"]
            horizon = config["periods_per_orbit"] * period
            options = dict(
                rtol=config["rtol"],
                atol_position=config["atol_position_km"],
                atol_velocity=config["atol_velocity_km_s"],
                max_step=period * config["max_step_period_fraction"],
                budget=run.check_budget,
            )
            checkpoint_path = run.folder / "checkpoints" / (item["orbit_id"] + ".json")

            def save_checkpoint(data):
                data.update(
                    model_sha256=run.ledger.used[config["model_path"]],
                    input_state_sha256=hashlib.sha256(
                        json.dumps(item["state"]).encode()
                    ).hexdigest(),
                    orbit_id=item["orbit_id"],
                )
                write_json(checkpoint_path, data)

            result = integrate(
                item["state"],
                gms,
                figures,
                radii,
                horizon,
                checkpoint_time=horizon * config["checkpoint_fraction"],
                checkpoint=save_checkpoint,
                **options,
            )
            tighter = integrate(
                item["state"],
                gms,
                figures,
                radii,
                horizon,
                **(options | dict(rtol=config["tighter_rtol"])),
            )
            last = np.array(result["final_state"])[-6:]
            refined = np.array(tighter["final_state"])[-6:]
            velocity_scale = math.sqrt(gms[0] / item["a_km"])
            dr = float(np.linalg.norm(last[:3] - refined[:3]) / item["a_km"])
            dv = float(np.linalg.norm(last[3:] - refined[3:]) / velocity_scale)
            checks[item["orbit_id"] + "_convergence"] = bool(
                dr <= config["trajectory_position_tolerance_a"]
                and dv <= config["trajectory_velocity_tolerance_na"]
            )
            checks[item["orbit_id"] + "_energy"] = (
                result["massive_energy_relative_drift"] <= config["energy_relative_tolerance"]
            )
            if result["event"] or tighter["event"]:
                checks[item["orbit_id"] + "_event_resolution"] = bool(
                    result["event"]
                    and tighter["event"]
                    and result["event"]["body_index"] == tighter["event"]["body_index"]
                    and abs(result["last_valid_time"] - tighter["last_valid_time"])
                    <= config["event_time_tolerance_period"] * period
                )
            if checkpoint_path.exists():
                name = checkpoint_path.relative_to(run.folder).as_posix()
                digest = sha256(checkpoint_path)
                run.manifest.setdefault("checkpoint_sha256", {})[name] = digest
                data = checkpoint_path.read_bytes()
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Checkpoint изменился перед восстановлением")
                cp = validate_checkpoint(
                    json.loads(data),
                    initial=item["state"],
                    orbit_id=item["orbit_id"],
                    model_sha256=run.ledger.used[config["model_path"]],
                    gms=gms,
                    horizon=horizon,
                    options=options,
                )
                resumed = integrate(
                    cp["state"], gms, figures, radii, horizon, t0=cp["time"], **options
                )
                checks[item["orbit_id"] + "_restart"] = bool(
                    resumed["final_state"] == result["final_state"]
                    and resumed["physical_outcome"] == result["physical_outcome"]
                )
            diagnostics = dict(position_difference_a=dr, velocity_difference_na=dv)
            if index % config["reference_every"] == 0:
                times = np.array([row["time"] for row in result["samples"]])
                reference = ias15_reference(
                    item["state"], gms, figures, times, config["ias15_epsilon"], run.check_budget
                )
                observed = np.array([row["probe_state"] for row in result["samples"]])
                ids_dr = float(
                    np.max(np.linalg.norm(reference[:, :3] - observed[:, :3], axis=1))
                    / item["a_km"]
                )
                ids_dv = float(
                    np.max(np.linalg.norm(reference[:, 3:] - observed[:, 3:], axis=1))
                    / velocity_scale
                )
                checks[item["orbit_id"] + "_IAS15"] = bool(
                    ids_dr <= config["trajectory_position_tolerance_a"]
                    and ids_dv <= config["trajectory_velocity_tolerance_na"]
                )
                diagnostics.update(ias15_position_a=ids_dr, ias15_velocity_na=ids_dv)
            if host == "himalia" and index % config["reference_every"] == 0:
                _, old_massive, _, _, _, _, _ = physical_inputs(host, model, original)
                paired = np.r_[old_massive.ravel(), np.array(item["state"])[-6:]]
                unaligned = integrate(
                    paired, gms, figures, radii, result["last_valid_time"], **options
                )
                diagnostics["alignment_sensitivity_position_a"] = float(
                    np.linalg.norm(np.array(unaligned["final_state"])[-6:-3] - last[:3])
                    / item["a_km"]
                )
                diagnostics["alignment_same_outcome"] = (
                    unaligned["physical_outcome"] == result["physical_outcome"]
                )
            outcome = dict(
                run_status="completed",
                physical_outcome=result["physical_outcome"],
                terminal_event=result["event"]["event"] if result["event"] else None,
                event_time=result["event"]["time"] if result["event"] else None,
                last_valid_time=result["last_valid_time"],
                uncertainty_status="conditional_reference_sphere_not_measured_surface_no_permanent_escape_claim",
            )
            initial = np.array(item["state"])[-6:]
            record = OrbitRecord.model_validate(
                dict(
                    run_id=run.run_id,
                    orbit_id=item["orbit_id"],
                    host_id=host,
                    model_version=model["model_id"],
                    inputs_sha256=dict(run.ledger.used),
                    initial_state=dict(
                        epoch=2451545.0,
                        time_scale="TDB",
                        origin=host,
                        frame="ICRF",
                        position_unit="km",
                        velocity_unit="km/s",
                        position=initial[:3].tolist(),
                        velocity=initial[3:].tolist(),
                    ),
                    sampling=dict(
                        domain_id=host + "-nominal-v1",
                        measure_id="deterministic_W0_baseline_counting_measure",
                        proposal_density=1.0 / len(selected),
                        target_density=1.0 / len(selected),
                        weight=1.0,
                        inclusion_probability=1.0,
                        stratum="baseline_development",
                        seed=0,
                        randomization=0,
                    ),
                    physics_config_sha256=run.ledger.used[config["model_path"]],
                    scenario_id="nominal_reference_spheres_J2_fixed_poles",
                    integrator="DOP853",
                    numeric_protocol_id=config["experiment_id"],
                    event_protocol_id="DOP853_all_polynomial_minima_v1",
                    outcome=outcome,
                )
            )
            product = dict(
                record=record.model_dump(),
                diagnostics=diagnostics,
                trajectory=result,
                horizon_seconds=horizon,
            )
            write_json(run.folder / "results" / (item["orbit_id"] + ".json"), product)
            outcomes.append(record.model_dump())
            costs.append(
                dict(
                    orbit_id=item["orbit_id"],
                    wall_seconds=result["wall_seconds"],
                    cpu_seconds=result["cpu_seconds"],
                    nfev=result["nfev"],
                    steps=result["steps"],
                    horizon_seconds=horizon,
                )
            )
            write_json(run.folder / "results/outcomes.partial.json", outcomes)
            print(
                json.dumps(
                    dict(
                        orbit=item["orbit_id"],
                        outcome=result["physical_outcome"],
                        wall_seconds=result["wall_seconds"],
                        dr=dr,
                        dv=dv,
                    )
                ),
                flush=True,
            )
        checks["complete_denominator"] = len(outcomes) == len(selected) and len(
            {row["orbit_id"] for row in outcomes}
        ) == len(selected)
        checks["independent_130_grids"] = all(
            len(
                json.loads(
                    (run.folder / "initial_conditions" / f"{h}_own_130_grid.json").read_text()
                )["records"]
            )
            == 130
            for h in config["hosts"]
        )
        validation = run.validate(
            checks,
            scope="W0_small_nominal_baseline_not_W1_population_or_W2_production",
            planned_count=len(planned),
            denominator=len(selected),
            limitations=[
                "Five initial periods per orbit, different physical horizons; no host survival comparison",
                "Contact-only reference sphere model; E2 crossings diagnostic, permanent escape not calibrated",
                "All outcomes are development data; no independent H1-H3 confirmation",
            ],
        )
        write_json(run.folder / "results/outcomes.json", outcomes)
        write_json(run.folder / "results/costs.json", costs)
        write_json(
            run.folder / "results/summary.json",
            dict(
                status=validation["status"],
                denominator=len(selected),
                planned_count=len(planned),
                outcomes={
                    value: sum(row["outcome"]["physical_outcome"] == value for row in outcomes)
                    for value in ("survived", "host_contact", "other_contact", "unresolved")
                },
                population_interpretation=False,
                physical_surface_verified=False,
                costs=costs,
            ),
        )
    print(
        json.dumps(dict(run_id=run.run_id, status=validation["status"], denominator=len(selected)))
    )
    return int(validation["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
