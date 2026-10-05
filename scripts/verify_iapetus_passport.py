"""Проверка паспорта Япета с общим lifecycle и SHA источников."""
import hashlib
import sys
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from submoon_research.catalog.passports import iapetus_source_measurements, verify_iapetus_passport
from submoon_research.workflows.baseline_audit import parse_initial_condition
from submoon_research.workflows.audit_runner import audit_cli, prepare_passport
from submoon_research.workflows.smoke import write_json


def execute(run, config):
    passport, readings, archive = prepare_passport(run, config)
    checks = dict(source_hashes=True, active_equals_versioned_passport=True)
    measured = iapetus_source_measurements(**readings)
    scientific_checks, metrics = verify_iapetus_passport(
        passport, measured, config["validation_limits"]["volume_radius_rounding_km"])
    checks.update(scientific_checks)
    with ZipFile(archive) as zipped:
        original = zipped.read(config["baseline_member"])
    parsed = parse_initial_condition(original.decode("utf-8"))
    host = next(row for row in parsed["states"] if row["name"] == "IAPETUS")
    au_text = run.ledger.text(config["au_source_path"])
    checks["au_exact_definition_source"] = "149597870700" in au_text and config["au_km"] == 149597870.7
    archive_radius = host["values"][1] * config["au_km"]
    checks["archival_radius_718_km"] = abs(archive_radius - 718) < 1e-8
    delta = passport["parameters"]["radius"]["value"] - archive_radius
    metrics.update({"archive_member": config["baseline_member"],
                    "archive_member_sha256": hashlib.sha256(original).hexdigest(),
                    "archive_radius_km": archive_radius, "primary_minus_archive_radius_km": delta,
                    "primary_minus_archive_fraction_of_primary": delta / measured["radius"],
                    "gm_1sigma_arithmetic_equivalent_km3_s2": measured["paper_gm_uncertainty"] / 3,
                    "independence_or_probability_distribution_assumed": False})

    run.check_budget()
    validation = run.validate({k: bool(v) for k,v in checks.items()}, scope=config['validation_limits']['scope'],
        metrics=metrics, limitations=['Не закрывает W0/W2; не разрешает production.',
            'Ковариации и физическая поверхность не получены; вероятностный ансамбль не допущен.'])
    write_json(run.folder / 'results/passport_audit.json', dict(run_id=run.run_id, **dict(passport=passport, source_measurements=measured, validation=validation)))
    return checks, metrics


def main():
    return audit_cli(__file__, 'W0-iapetus', 'configs/audits/Iapetus_passport_v1.yaml', execute)


if __name__ == '__main__':
    raise SystemExit(main())
