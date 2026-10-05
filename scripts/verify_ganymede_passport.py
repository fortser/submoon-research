"""Проверка паспорта Ганимеда с общим lifecycle и SHA источников."""
import sys
from pathlib import Path
from zipfile import ZipFile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from submoon_research.catalog.ganymede_passport import ganymede_source_measurements, verify_ganymede_passport, audit_ganymede_archive
from submoon_research.workflows.audit_runner import audit_cli, prepare_passport
from submoon_research.workflows.smoke import write_json


def execute(run, config):
    passport, readings, archive = prepare_passport(run, config)
    checks = dict(source_hashes=True, active_equals_versioned_passport=True)
    source = ganymede_source_measurements(**readings)
    checks.update(verify_ganymede_passport(passport, source))
    checks["au_exact_conversion_source"] = config["au_km"] == 149597870.7 and "149597870700" in run.ledger.text(config['au_source_path'])
    with ZipFile(archive) as zipped:
        archive_result = audit_ganymede_archive(zipped, source, config["au_km"])
    checks["all_130_inputs_read"] = archive_result["archive_members_checked"] == 130
    checks["one_submoon_per_input"] = archive_result["one_submoon_per_input"]
    checks["host_origin_zero"] = archive_result["host_origin_zero"]
    checks["known_heterogeneity_reproduced"] = archive_result["massive_state_group_counts"] == config["expected_group_counts"]
    checks["exact_six_nonmodal_members"] = set(archive_result["nonmodal_members"]) == {
        f"Jupiter/GANYMEDE/sim_{index}.txt" for index in config["expected_nonmodal_indices"]}
    checks["nonmodal_not_only_rotation"] = all(any(abs(item["delta_distance_km"]) > 1
        for item in group["distance_invariants"]) for group in archive_result["differences"])
    comparisons = archive_result["mass_radius_comparisons"]
    checks["archive_radius_2631_km"] = all(abs(item["archive_radius_km"] - 2631) < 1e-8 for item in comparisons)
    checks["all_host_parent_mass_ratios_match_with_roundoff"] = all(
        abs(item["archive_mass_ratio"] / comparisons[0]["archive_mass_ratio"] - 1) < 1e-14 for item in comparisons)
    checks["archive_disposition_preserves_originals"] = passport["archive_disposition"]["action"] == archive_result["disposition"]
    metrics = dict(archive_radius_km=comparisons[0]["archive_radius_km"],
        compiled_minus_archive_radius_km=passport["parameters"]["radius"]["value"] - comparisons[0]["archive_radius_km"],
        archive_to_primary_mass_factor=comparisons[0]["archive_to_primary_mass_factor"],
        modal_count=len(archive_result["modal_members"]), nonmodal_members=archive_result["nonmodal_members"],
        massive_state_versions=len(archive_result["massive_state_group_counts"]),
        field_versions=archive_result["field_versions"], archive_members_checked=130)

    run.check_budget()
    validation = run.validate({k: bool(v) for k,v in checks.items()}, scope=config['validation_limits']['scope'],
        metrics=metrics, limitations=['Не закрывает W0/W2; не разрешает production.',
            'Ковариации и физическая поверхность не получены; вероятностный ансамбль не допущен.'])
    write_json(run.folder / 'results/passport_audit.json', dict(run_id=run.run_id, **dict(passport=passport, source_measurements=source, archive=archive_result, validation=validation)))
    return checks, metrics


def main():
    return audit_cli(__file__, 'W0-ganymede', 'configs/audits/ganymede_passport_v1_0_1.yaml', execute)


if __name__ == '__main__':
    raise SystemExit(main())
