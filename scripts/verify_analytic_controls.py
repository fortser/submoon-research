"""V02: независимое решение Кеплера и два интегратора; не реальный пилот."""

import sys
import math
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from submoon_research.execution import RunContext
from submoon_research.workflows.smoke import write_json
from submoon_research.dynamics.kepler_reference import pericenter_seed, pericenter_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=Path("configs/validation/W2_analytic_v2.yaml")
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, "W2-analytic", wall_seconds=120, output_mib=16)
    with run:
        run.save_code(Path(__file__).resolve())
        config = run.ledger.yaml(args.config, registered=True)
        run.save_config(config)
        import numpy as np
        import rebound
        from scipy.integrate import solve_ivp
        from scipy.optimize import brentq

        results = []
        checks = {}
        for e in config["eccentricities"]:
            # Выходы сгущены около каждого перицентра; эталон не использует генератор W1.
            period = 2 * math.pi
            ordinary = np.linspace(0, config["periods"] * period, config["output_samples"])
            offsets = np.array([-1.0, -0.1, 0.0, 0.1, 1.0]) * (1 - e) ** 1.5
            close = np.concatenate([k * period + offsets for k in range(config["periods"] + 1)])
            times = np.unique(np.r_[ordinary, close[(close >= 0) & (close <= ordinary[-1])]])
            if config["schema_version"] == "0.1":
                analytic = []
                for time in times:
                    m = time % period
                    E = brentq(lambda x: x - e * np.sin(x) - m, 0, period, xtol=1e-14) if m else 0.0
                    c, s = np.cos(E), np.sin(E)
                    analytic.append(
                        [
                            c - e,
                            np.sqrt(1 - e * e) * s,
                            0.0,
                            -s / (1 - e * c),
                            np.sqrt(1 - e * e) * c / (1 - e * c),
                            0.0,
                        ]
                    )
                analytic = np.asarray(analytic)
                initial = analytic[0]
            else:
                initial = pericenter_seed(e)
                analytic, reference = pericenter_reference(
                    initial, times, digits=config["reference_digits"]
                )
                independently, other = pericenter_reference(initial, times, digits=80)
                checks[f"e{e}_reference_precision"] = bool(
                    np.max(np.abs(analytic - independently)) <= 1e-14
                    and reference["kepler_residual"] < 1e-40
                )
                write_json(
                    run.folder / "results" / f"reference_e{e}.json",
                    dict(reference=reference, other_precision=other),
                )

            def rhs(t, y):
                run.check_budget()
                return np.r_[y[3:], -y[:3] / np.linalg.norm(y[:3]) ** 3]

            for rtol in config["dop853_rtols"]:
                sol = solve_ivp(
                    rhs,
                    (0, times[-1]),
                    initial,
                    method="DOP853",
                    rtol=rtol,
                    atol=rtol * config["dop853_atol_factor"],
                    t_eval=times,
                )
                if not sol.success:
                    raise RuntimeError(sol.message)
                results.append(measure(e, "DOP853", rtol, sol.y.T, analytic, sol.nfev))
                write_json(run.folder / "results/convergence.partial.json", results)
            sim = rebound.Simulation()
            sim.G = 1.0
            sim.add(m=1.0)
            sim.add(m=0.0, x=float(initial[0]), vy=float(initial[4]))
            sim.integrator = "ias15"
            sim.integrator.epsilon = config["ias15_epsilon"]
            sim.integrator.adaptive_mode = config["ias15_adaptive_mode"]
            if "ias15_initial_dt" in config:
                sim.dt = config["ias15_initial_dt"]
            states = []
            for time in times:
                run.check_budget()
                sim.integrate(float(time))
                p = sim.particles[1]
                states.append([p.x, p.y, p.z, p.vx, p.vy, p.vz])
            results.append(
                measure(e, "IAS15", config["ias15_epsilon"], np.asarray(states), analytic, None)
            )
            write_json(run.folder / "results/convergence.partial.json", results)
            strict = [
                r
                for r in results
                if r["e"] == e
                and (
                    r["integrator"] == "IAS15"
                    or r["accuracy_parameter"] == min(config["dop853_rtols"])
                )
            ]
            for result in strict:
                result["admitted_domain"] = e in config.get("admission", {}).get(
                    result["integrator"], config["eccentricities"]
                )
                result["tolerance_passed"] = all(
                    result[k] <= v for k, v in config["tolerances"].items()
                )
                if not result["admitted_domain"]:
                    continue
                for key, tolerance in config["tolerances"].items():
                    checks[f"e{e}_{result['integrator']}_{key}"] = bool(result[key] <= tolerance)
            write_json(
                run.folder / "initial_conditions" / f"e{e}.json",
                dict(data_kind="synthetic", initial=initial.tolist(), times=times.tolist()),
            )
        validation = run.validate(
            checks,
            scope="V02_synthetic_two_body_only",
            tolerances=config["tolerances"],
            limitations=[
                "Не проверяет реальные параметры, ансамбль хозяев, события или restart.",
                "Допуск метода относится только к явно перечисленным эксцентриситетам; DOP853 при высоком e не принят.",
                "IAS15 epsilon не равен rtol DOP853; критерии не являются production допусками.",
            ],
        )
        write_json(run.folder / "results/convergence.json", results)
        write_json(
            run.folder / "environment/ias15_documentation.json",
            dict(
                rebound_version=rebound.__version__,
                documentation=sim.integrator.__doc__,
                epsilon=sim.integrator.epsilon,
                adaptive_mode=str(sim.integrator.adaptive_mode),
            ),
        )
    print(run.run_id, validation["status"])
    return int(validation["status"] != "passed")


def measure(e, integrator, accuracy, states, analytic, nfev):
    import numpy as np

    energy = np.sum(states[:, 3:] ** 2, axis=1) / 2 - 1 / np.linalg.norm(states[:, :3], axis=1)
    angular = np.linalg.norm(np.cross(states[:, :3], states[:, 3:]), axis=1)
    dr = np.linalg.norm(states[:, :3] - analytic[:, :3], axis=1)
    dv = np.linalg.norm(states[:, 3:] - analytic[:, 3:], axis=1)
    return dict(
        e=e,
        integrator=integrator,
        accuracy_parameter=accuracy,
        nfev=nfev,
        position=float(dr.max()),
        velocity=float(dv.max()),
        energy=float(np.abs(energy / (-0.5) - 1).max()),
        angular_momentum=float(np.abs(angular / np.sqrt(1 - e * e) - 1).max()),
        pericenter_scaled_position=float(dr.max() / (1 - e)),
        pericenter_scaled_velocity=float(dv.max() / np.sqrt((1 + e) / (1 - e))),
    )


if __name__ == "__main__":
    raise SystemExit(main())
