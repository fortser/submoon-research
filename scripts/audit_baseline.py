"""???????????? ????? W0 ? ?????? manifest ? ???????????? ???????."""
import hashlib
import sys
from io import BytesIO
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from submoon_research.workflows.baseline_audit import archive_audit, bundle_inventory, html_rows, jpl_parameter_rows, numeric_audit, paper_physical_parameters
from submoon_research.workflows.audit_runner import audit_cli
from submoon_research.workflows.smoke import write_json


def execute(run, config):
    root, ledger = run.root, run.ledger
    receipts = ledger.json(config['source_receipts'], registered=True)['receipts']
    ledger.bind_manifest(dict(files=receipts))
    ledger.verify_all()
    inventory, text = bundle_inventory(root, config["bundle"], ledger)
    paper = paper_physical_parameters(text)
    receipts = ledger.json(config["source_receipts"], registered=True)["receipts"]
    by_source = {item["source_id"]: item for item in receipts}
    jpl_source = by_source["jpl_satellite_parameters_20261004"]
    jpl = jpl_parameter_rows(ledger.text(jpl_source["path"]),
                             config["jpl_comparison_hosts"])
    g_source = by_source[config["g_source_id"]]
    g_rows = html_rows(ledger.text(g_source["path"]))
    g_row = next(row for row in g_rows if row and row[0] == "Newtonian constant of gravitation")
    if "6.67430" not in g_row[2] or "0.00015" not in g_row[2]:
        raise ValueError("Configured G was not found in the captured primary reference")
    numeric = numeric_audit(paper, jpl, config["g_si"])
    metadata_source = by_source["baseline_initial_conditions_zenodo"]
    metadata = ledger.json(metadata_source["path"])
    checks = dict(inventory["checks"])
    checks.update({
        "source_receipt_hashes": all(item["status"] == "retrieved"
            and ledger.used[item["path"]] == item["sha256"] for item in receipts),
        "bundle_file_count": len(inventory["files"]) == config["validation_limits"]["expected_bundle_files"],
        "figure_count": len(inventory["referenced_figures"]) == config["validation_limits"]["expected_figures"],
        "pilot_rows_present": all(name in paper for name in config["validation_limits"]["expected_pilot_hosts"]),
        "explicit_required_text": all(fragment in text for fragment in
            config["documentary_checks"]["required_text_fragments"]),
        "zenodo_title_matches": inventory["identity"]["title"] in metadata["metadata"]["title"],
        "zenodo_record_id": metadata["id"] == 19820511,
    })
    archive = None
    archive_receipt = None
    if config["archive_receipt"] is not None:
        archive_receipt = ledger.json(config["archive_receipt"], registered=True)
        ledger.bind(archive_receipt["path"], archive_receipt["sha256"])
        archive_bytes = ledger.read(archive_receipt["path"])
        archive_path = BytesIO(archive_bytes)
        checks["archive_sha256"] = True
        checks["archive_declared_size"] = len(archive_bytes) == metadata["files"][0]["size"]
        checks["archive_publisher_md5"] = (
            "md5:" + hashlib.md5(archive_bytes).hexdigest() == metadata["files"][0]["checksum"])
        archive = archive_audit(archive_path, ["IAPETUS", "GANYMEDE", "HIMALIA"])
        checks.update({"archive_" + key: value for key, value in archive["checks"].items()})
        checks["archive_host_count"] = archive["host_count"] == config["validation_limits"]["expected_archive_hosts"]
        checks["archive_input_count"] = archive["input_file_count"] == config["validation_limits"]["expected_archive_inputs"]
    checks = {key: bool(value) for key, value in checks.items()}

    run.check_budget()
    validation = run.validate(checks, scope=config['validation_limits']['scope'],
        limitations=['?? ????????? ????????; ?????????????? ???????? ??????? ????????? failed.',
            '??????????? ????? ???????/???/G ?? ?????????????.'])
    write_json(run.folder / 'results/audit.json', dict(schema_version='0.2', run_id=run.run_id,
        experiment_id=config['experiment_id'], source_bundle=inventory,
        paper_physical_parameters=paper, numeric_audit=numeric,
        external_sources=receipts, archive_receipt=archive_receipt, archive_audit=archive, validation=validation))
    return checks, numeric


def main():
    return audit_cli(__file__, 'W0-audit', 'configs/audits/W0_baseline_v1.yaml', execute)


if __name__ == '__main__':
    raise SystemExit(main())
