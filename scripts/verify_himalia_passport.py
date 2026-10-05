"""Проверка паспорта Гималии с общим lifecycle и SHA источников."""
import hashlib
import sys
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from submoon_research.catalog.himalia_passport import himalia_source_measurements, verify_himalia_passport, himalia_archive_comparison
from submoon_research.workflows.baseline_audit import parse_initial_condition
from submoon_research.workflows.audit_runner import audit_cli, prepare_passport
from submoon_research.workflows.smoke import write_json


def execute(run, config):
    passport, readings, archive = prepare_passport(run, config)
    checks = dict(source_hashes=True, active_equals_versioned_passport=True)
    source = himalia_source_measurements(**readings)
    scientific_checks, metrics = verify_himalia_passport(passport, source)
    checks.update(scientific_checks)
    constants = run.ledger.text(config["au_source_path"])
    checks["au_and_g_source"] = ("149597870700" in constants and "6.67430" in constants
        and config["au_km"] == 149597870.7 and config["g_si"] == 6.67430e-11)
    comparisons, members = [], []
    with ZipFile(archive) as zipped:
        for index in range(1, 131):
            run.check_budget()
            member = config["baseline_prefix"] + f"sim_{index}.txt"
            data = zipped.read(member)
            parsed = parse_initial_condition(data.decode("utf-8"))
            comparisons.append(himalia_archive_comparison(parsed["states"], source, config["au_km"], config["g_si"]))
            members.append({"member": member, "sha256": hashlib.sha256(data).hexdigest()})
    checks["all_130_archive_mass_radius_rows_equal"] = all(c == comparisons[0] for c in comparisons)
    checks["archive_radius_80_km"] = all(abs(c["archive_radius_km"] - 80) < 1e-8 for c in comparisons)
    paper = run.ledger.text(config["paper_path"])
    checks["paper_mass_and_radius_documented"] = (
        r"Himalia & 80 & 9.50$\times 10^{18 }$" in paper and config["baseline_paper_mass_kg"] == 9.50e18)
    metrics.update(comparisons[0])
    metrics.update(archive_members_checked=len(members), archive_member_hashes=members,
        paper_mass_kg=config["baseline_paper_mass_kg"],
        paper_mass_to_nominal_gm_equivalent_ratio=config["baseline_paper_mass_kg"] / metrics["nominal_gm_equivalent_mass_kg"],
        iau_minus_archive_radius_km=passport["parameters"]["radius"]["value"] - metrics["archive_radius_km"],
        thermal_minus_archive_radius_km=passport["size_constraints"]["thermal_effective_radius"]["value"] - metrics["archive_radius_km"])

    run.check_budget()
    validation = run.validate({k: bool(v) for k,v in checks.items()}, scope=config['validation_limits']['scope'],
        metrics=metrics, limitations=['Не закрывает W0/W2; не разрешает production.',
            'Ковариации и физическая поверхность не получены; вероятностный ансамбль не допущен.'])
    write_json(run.folder / 'results/passport_audit.json', dict(run_id=run.run_id, **dict(passport=passport, source_measurements=source, validation=validation)))
    return checks, metrics


def main():
    return audit_cli(__file__, 'W0-himalia', 'configs/audits/himalia_passport_v1.yaml', execute)


if __name__ == '__main__':
    raise SystemExit(main())
