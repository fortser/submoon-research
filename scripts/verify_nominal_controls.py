"""CR3BP/Якоби и аналитическая прецессия J2: независимые эталоны до W0 ансамбля."""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import numpy as np
from scipy.integrate import solve_ivp
from submoon_research.execution import RunContext
from submoon_research.forces.gravity import j2_acceleration
from submoon_research.sampling.design import elements_to_cartesian
from submoon_research.workflows.smoke import write_json


def main():
    root = Path(__file__).resolve().parents[1]
    run = RunContext(root, "W2-nominal-controls", wall_seconds=120, output_mib=16)
    with run:
        run.save_code(Path(__file__).resolve())
        cfg = run.ledger.yaml("configs/validation/W2_nominal_controls_v1.yaml", registered=True)
        run.save_config(cfg)
        numeric = cfg["integration"]
        mu = cfg["cr3bp"]["mu"]
        checks = {}
        metrics = {}

        def restricted(t, y):
            run.check_budget()
            x, z, vx, vz = y
            r1 = np.hypot(x + mu, z)
            r2 = np.hypot(x - 1 + mu, z)
            return [
                vx,
                vz,
                2 * vz + x - (1 - mu) * (x + mu) / r1**3 - mu * (x - 1 + mu) / r2**3,
                -2 * vx + z - (1 - mu) * z / r1**3 - mu * z / r2**3,
            ]

        l4 = np.array([0.5 - mu, math.sqrt(3) / 2, 0.0, 0.0])
        checks["CR3BP_L4_equilibrium"] = bool(
            np.linalg.norm(restricted(0, l4)) < cfg["cr3bp"]["equilibrium_tolerance"]
        )
        l5 = l4.copy()
        l5[1] *= -1
        checks["CR3BP_L5_equilibrium"] = bool(
            np.linalg.norm(restricted(0, l5)) < cfg["cr3bp"]["equilibrium_tolerance"]
        )
        initial = l4 + np.array([0.005, 0.0, 0.0, -0.001])
        times = np.linspace(0, cfg["cr3bp"]["periods"] * 2 * math.pi, 1025)
        sol = solve_ivp(
            restricted,
            (0, times[-1]),
            initial,
            t_eval=times,
            method="DOP853",
            rtol=numeric["rtol"],
            atol=numeric["atol"],
            max_step=2 * math.pi * numeric["max_step_orbit_fraction"],
        )
        x, y, vx, vy = sol.y
        jacobi = (
            x * x
            + y * y
            + 2 * (1 - mu) / np.hypot(x + mu, y)
            + 2 * mu / np.hypot(x - 1 + mu, y)
            - vx * vx
            - vy * vy
        )
        drift = float(np.max(abs(jacobi / jacobi[0] - 1)))
        checks["CR3BP_Jacobi"] = bool(
            sol.success and drift < cfg["cr3bp"]["jacobi_relative_tolerance"]
        )
        metrics["cr3bp"] = dict(jacobi_relative_drift=drift, nfev=sol.nfev)
        jcfg = cfg["j2"]
        values = []
        for j2 in jcfg["values"]:
            initial = elements_to_cartesian(
                1.0,
                jcfg["a"],
                jcfg["e"],
                math.cos(jcfg["inclination_rad"]),
                0.0,
                0.0,
                0.0,
                np.eye(3),
            )

            def rhs(t, y):
                run.check_budget()
                return np.r_[
                    y[3:],
                    -y[:3] / np.linalg.norm(y[:3]) ** 3
                    + j2_acceleration(y[:3], 1.0, j2, jcfg["radius"], [0.0, 0.0, 1.0]),
                ]

            period = 2 * math.pi * jcfg["a"] ** 1.5
            times = np.linspace(0, jcfg["periods"] * period, 4097)
            sol = solve_ivp(
                rhs,
                (0, times[-1]),
                initial,
                t_eval=times,
                method="DOP853",
                rtol=numeric["rtol"],
                atol=numeric["atol"],
                max_step=period * numeric["max_step_orbit_fraction"],
            )
            r, v = sol.y[:3].T, sol.y[3:].T
            radius = np.linalg.norm(r, axis=1)
            angular = np.cross(r, v)
            node = np.cross(np.tile([0.0, 0.0, 1.0], (len(r), 1)), angular)
            eccentric = np.cross(v, angular) - r / radius[:, None]
            longitude = np.unwrap(np.arctan2(node[:, 1], node[:, 0]))
            pericenter = np.unwrap(
                np.arctan2(
                    np.sum(np.cross(node, eccentric) * angular, axis=1)
                    / np.linalg.norm(angular, axis=1),
                    np.sum(node * eccentric, axis=1),
                )
            )
            node_rate = float(np.polyfit(times, longitude, 1)[0])
            peri_rate = float(np.polyfit(times, pericenter, 1)[0])
            scale = j2 * jcfg["radius"] ** 2 / (jcfg["a"] ** 3.5 * (1 - jcfg["e"] ** 2) ** 2)
            expected_node = -1.5 * scale * math.cos(jcfg["inclination_rad"])
            expected_peri = 0.75 * scale * (5 * math.cos(jcfg["inclination_rad"]) ** 2 - 1)
            energy = (
                np.sum(v * v, axis=1) / 2
                - 1 / radius
                + j2 * jcfg["radius"] ** 2 * (3 * (r[:, 2] / radius) ** 2 - 1) / (2 * radius**3)
            )
            result = dict(
                j2=j2,
                node_rate=node_rate,
                analytic_node_rate=expected_node,
                peri_rate=peri_rate,
                analytic_peri_rate=expected_peri,
                node_relative_error=abs(node_rate / expected_node - 1),
                peri_relative_error=abs(peri_rate / expected_peri - 1),
                energy_relative_drift=float(np.max(abs(energy / energy[0] - 1))),
                nfev=sol.nfev,
            )
            for key in ("node", "peri"):
                checks[f"J2_{j2}_{key}_precession"] = bool(
                    sol.success
                    and result[key + "_relative_error"] < jcfg["rate_relative_tolerance"]
                )
            checks[f"J2_{j2}_energy"] = (
                result["energy_relative_drift"] < jcfg["energy_relative_tolerance"]
            )
            values.append(result)
        metrics["j2"] = values
        validation = run.validate(
            checks, scope="V03_CR3BP_and_V04_J2_precession_only", metrics=metrics, tolerances=cfg
        )
        write_json(run.folder / "results/controls.json", metrics)
    print(json.dumps(dict(run_id=run.run_id, status=validation["status"], metrics=metrics)))
    return int(validation["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
