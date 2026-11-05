"""Независимые арифметические контроли и отрицательная проба привязки L1 к SHA."""
from __future__ import annotations

import csv
from decimal import Decimal, localcontext
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid
from zipfile import ZipFile

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from submoon_research.tracking import utc_now
from submoon_research.workflows.smoke import environment, sha256, write_json

ROOT = Path(__file__).resolve().parents[1]


def native_rows(text):
    # Независимое чтение только раздела состояний; функции baseline_audit не вызываются.
    body = text.split("Name,Mass,Radius,X,Y,Z,VX,VY,VZ", 1)[1].split("#", 1)[0]
    result = {}
    for row in csv.reader(body.strip().splitlines()):
        if row:
            result[row[0]] = [float(x) for x in row[1:]]
    return result


def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]


def dot(a, b):
    return sum(x*y for x, y in zip(a, b, strict=True))


def archive_metrics(path):
    result = {}
    with ZipFile(path) as archive:
        members = [n for n in archive.namelist() if "/sim_" in n and n.endswith(".txt")]
        for planet, host in [("Saturn", "IAPETUS"), ("Jupiter", "GANYMEDE"),
                             ("Jupiter", "HIMALIA"), ("Saturn", "HYPERION")]:
            records = [native_rows(archive.read(f"{planet}/{host}/sim_{i}.txt").decode()) for i in range(1, 131)]
            radii, inferred_g, inclinations, radial_speeds, roche_values = [], [], [], [], []
            groups = {}
            for index, bodies in enumerate(records, 1):
                sub = next(v for k, v in bodies.items() if k.startswith("Sat_"))
                h, p = bodies[host], bodies[planet.upper()]
                r, v = sub[2:5], sub[5:8]
                distance = math.sqrt(dot(r, r))
                radii.append(distance)
                inferred_g.append(dot(v, v)*distance/(h[0]+sub[0]))
                radial_speeds.append(dot(r, v)/distance)
                a, b = cross(r, v), cross(p[2:5], p[5:8])
                cosine = dot(a, b)/math.sqrt(dot(a, a)*dot(b, b))
                inclinations.append(math.degrees(math.acos(max(-1, min(1, cosine)))))
                # Из определения плотности и формулы статьи: r_R = R_b*(2*m_s/m_b)^(1/3).
                roche_values.append(sub[1]*(2*h[0]/sub[0])**(1/3))
                key = json.dumps({k:v for k,v in bodies.items() if not k.startswith("Sat_")}, sort_keys=True)
                groups.setdefault(key, []).append(index)
            modal_key = max(groups, key=lambda key: len(groups[key]))
            modal = json.loads(modal_key)
            h, p = modal[host], modal[planet.upper()]
            gm = statistics.median(inferred_g)*(h[0]+p[0])
            distance = math.sqrt(dot(p[2:5], p[2:5]))
            semi = 1/(2/distance-dot(p[5:8],p[5:8])/gm)
            hill = semi*(h[0]/(3*p[0]))**(1/3)
            result[host] = dict(files=len(records), groups=len(groups),
                nonmodal=sorted(i for k, values in groups.items() if k != modal_key for i in values),
                min_radius_au=min(radii), max_radius_au=max(radii),
                roche_au=roche_values[0], lower_to_rigid_roche=min(radii)/roche_values[0],
                upper_to_09309_circular_hill=max(radii)/(0.9309*hill),
                inferred_g_median=statistics.median(inferred_g),
                inferred_g_relative_span=(max(inferred_g)-min(inferred_g))/statistics.median(inferred_g),
                max_abs_radial_velocity_au_day=max(abs(v) for v in radial_speeds),
                distinct_inclinations_deg=sorted({round(i, 5) for i in inclinations}),
                host_mass_solar_range=[min(b[host][0] for b in records), max(b[host][0] for b in records)],
                assumption="Диагностика кругового старта; неизвестный авторский G не подтверждается.")
    return dict(input_files=len(members), pilots_and_hyperion=result,
                sphere_mass_kg=4*math.pi/3*1000*100**3,
                appendix_relative_error=4.9e9/(4*math.pi/3*1000*100**3)-1)


def decimal_geometry():
    base = ROOT / "runs/W0-states-20261004T104417Z-eacbd97e/acquisition"
    processed = json.loads((ROOT / "data/processed/W0-geometric-states-v1/geometric_states.json").read_text())
    vectors = {}
    with localcontext() as ctx:
        ctx.prec = 45
        for folder in base.iterdir():
            text = (folder / "astrobridge/horizons.txt").read_text(encoding="utf-8")
            head, tail = text.split("$$SOE", 1)
            columns = next(csv.reader(next(line for line in reversed(head.splitlines()) if "JDTDB" in line and "," in line).splitlines()))
            columns = [c.strip() for c in columns]
            rows = list(csv.reader(tail.split("$$EOE")[0].strip().splitlines()))
            vectors[folder.name] = [[Decimal(row[columns.index(key)].strip())*Decimal("149597870.7")
                 /(Decimal(86400) if key.startswith("V") else 1)
                 for key in ("X", "Y", "Z", "VX", "VY", "VZ")] for row in rows]
        checks = {}
        for host, parent in [("iapetus", "saturn"), ("ganymede", "jupiter"), ("himalia", "jupiter")]:
            delta = [[vectors[parent][i][j]-vectors[host][i][j]-vectors[f"direct_{host}"][i][j]
                      for j in range(6)] for i in range(2)]
            positions = [sum(x*x for x in d[:3]).sqrt() for d in delta]
            velocities = [sum(x*x for x in d[3:]).sqrt() for d in delta]
            saved = processed["host_relative"][host][parent]
            floating_delta = [abs(Decimal(str(saved[i][j]))-(vectors[parent][i][j]-vectors[host][i][j]))
                              for i in range(2) for j in range(3)]
            checks[host] = dict(decimal_direct_position_max_km=str(max(positions)),
                decimal_direct_velocity_max_km_s=str(max(velocities)),
                saved_float_vs_decimal_position_component_max_km=str(max(floating_delta)),
                passed=max(positions) <= Decimal("0.001") and max(velocities) <= Decimal("1e-8"))
        return checks


def main():
    started = utc_now()
    run_id = "W0-review-independent-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    folder = ROOT / "runs" / run_id
    folder.mkdir(exist_ok=False)
    archive = ROOT / "data/raw/W0-baseline-inputs-20261004T075626Z/public_files.zip"
    spec_path = ROOT / "configs/experiments/L1_baseline_spec_v0_2.yaml"
    state_path = ROOT / "data/processed/W0-geometric-states-v1/geometric_states.json"
    inputs = {p.relative_to(ROOT).as_posix(): sha256(p) for p in (archive, spec_path, state_path)}
    metrics = archive_metrics(archive)
    write_json(folder / "results/independent_archive.json", metrics)
    write_json(folder / "results/independent_decimal_geometry.json", decimal_geometry())
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    states = json.loads(state_path.read_text(encoding="utf-8"))
    # Отрицательный пример: испорчен только вектор Солнца; относительные состояния не изменены.
    states["barycentric"]["sun"][0][0] += 1000000
    states["data_kind"] = "synthetic_invalid_input_probe"
    states["synthetic"] = True
    fake_state = folder / "inputs/synthetic_inconsistent_states.json"
    write_json(fake_state, states)
    spec["routes"]["independent_reproduction"]["states"]["path"] = fake_state.relative_to(ROOT).as_posix()
    fake_config = folder / "inputs/synthetic_unbound_L1.yaml"
    fake_config.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    env = os.environ.copy()
    env.update(PYTHONIOENCODING="utf-8", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    process = subprocess.run([sys.executable, "-X", "utf8", "scripts/verify_l1_specification.py", "--config",
        fake_config.relative_to(ROOT).as_posix()], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", env=env, timeout=120, check=False)
    (folder / "probe_stdout.txt").write_text(process.stdout + process.stderr, encoding="utf-8")
    outcome = json.loads(process.stdout)
    probe_manifest = json.loads((ROOT / "runs" / outcome["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    result = dict(data_kind="synthetic_invalid_input_probe", cli_exit_code=process.returncode,
        probe_run_id=outcome["run_id"], validation=outcome["validation"], checks=outcome["checks"],
        input_is_hash_bound=fake_state.relative_to(ROOT).as_posix() in probe_manifest["inputs_sha256"],
        modified_position_component_km=1000000, actual_input_path=fake_state.relative_to(ROOT).as_posix(),
        actual_input_sha256=sha256(fake_state),
        actual_input_copied=(ROOT / "runs" / outcome["run_id"] / "inputs" / fake_state.relative_to(ROOT)).is_file())
    write_json(folder / "results/unbound_input_probe.json", result)
    code_path = folder / "code/scripts/review_w0_independent.py"
    code_path.parent.mkdir(parents=True, exist_ok=True)
    code_path.write_bytes(Path(__file__).read_bytes())
    config = dict(experiment_id="W0-independent-review-v1", network_calls=0, paid_calls=0,
                  scientific_dynamics=False, data_kind="source_diagnostics_and_synthetic_negative_probe")
    (folder / "config.resolved.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    write_json(folder / "validation.json", dict(status="failed", audit_execution="completed_with_findings",
        unbound_input_rejected=process.returncode != 0))
    science = dict(config=config, inputs=inputs, code=sha256(Path(__file__)), environment=environment())
    write_json(folder / "manifest.json", dict(schema_version="0.1", run_id=run_id,
        experiment_id=config["experiment_id"], status="completed", started_utc=started,
        finished_utc=utc_now(), data_kind=config["data_kind"], validation={"status":"failed"},
        scientific_id=hashlib.sha256(json.dumps(science, sort_keys=True).encode()).hexdigest(),
        inputs_sha256=inputs, code_sha256={"scripts/review_w0_independent.py":sha256(Path(__file__))},
        environment=environment(), resources=dict(workers=1,network_calls=0,paid_calls=0),
        artifacts_sha256={p.relative_to(folder).as_posix():sha256(p) for p in folder.rglob("*") if p.is_file()}))
    print(json.dumps(dict(run_id=run_id, unbound_input_probe=result, archive=metrics), ensure_ascii=False))


if __name__ == "__main__":
    main()
