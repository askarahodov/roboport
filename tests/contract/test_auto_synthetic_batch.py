"""Contract for two-input offline synthetic batch and no-partial-output behavior."""
import hashlib
import os
import zipfile
from uuid import uuid4
from xml.sax.saxutils import escape

from scripts.auto_synthetic_batch import inspect_synthetic_batch
from scripts.auto_vehicle_migration_preflight import COLUMNS
from roboport_api.auto_service_import import FIELDS

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(path, headers, values):
    rows = []
    for n, fields in enumerate((headers, values), 1):
        cells = "".join(
            f'<c r="{chr(65+i)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
            for i, v in enumerate(fields)
        )
        rows.append(f'<row r="{n}">{cells}</row>')
    worksheet = f'<worksheet xmlns="{NS}"><sheetData>{"".join(rows)}</sheetData></worksheet>'
    root = f'<workbook xmlns="{NS}"><sheets><sheet name="Import" sheetId="1"/></sheets></workbook>'
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", root)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def fixtures(tmp_path):
    sources = {"VEHICLE": tmp_path / "vehicles.xlsx", "SERVICE": tmp_path / "services.xlsx"}
    workbook(sources["VEHICLE"], COLUMNS,
             ("VEHICLE", "SRC-1", str(uuid4()), str(uuid4()),
              "1HGCM82633A004381", "SYNTHETIC", "TEST CAR", "DEMO"))
    workbook(sources["SERVICE"], FIELDS,
             ("SERVICE", "DEMO", "SRC-1", "BRAKE-TEST", "SYNTHETIC", "30"))
    digests = {kind: hashlib.sha256(path.read_bytes()).hexdigest()
               for kind, path in sources.items()}
    return sources, digests


def test_successful_two_domain_batch_is_reconciled_and_private(tmp_path):
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    result = inspect_synthetic_batch(sources, output, expected_sha256=digests)
    assert result["ok"] and not result["release_allowed"] and result["writes"] == 0
    assert result["rows"] == {"VEHICLE": 1, "SERVICE": 1}
    for kind in sources:
        path = output / (kind.lower() + ".csv")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"][kind]
        assert path.stat().st_mode & 0o777 == 0o600
    assert not list(output.glob(".auto-batch-*"))


def test_invalid_second_input_cannot_publish_first(tmp_path):
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    sources["SERVICE"].write_bytes(sources["SERVICE"].read_bytes() + b"tamper")
    result = inspect_synthetic_batch(sources, output, expected_sha256=digests)
    assert not result["ok"] and result["code"] == "SOURCE_CHECKSUM"
    assert not list(output.iterdir())
    assert "SRC-1" not in str(result)


def test_existing_target_does_not_overwrite_or_publish_other(tmp_path):
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    (output / "service.csv").write_bytes(b"previous")
    result = inspect_synthetic_batch(sources, output, expected_sha256=digests)
    assert result["code"] == "OUTPUT_CONFLICT"
    assert (output / "service.csv").read_bytes() == b"previous"
    assert not (output / "vehicle.csv").exists()


def test_failure_publishing_second_rolls_back_first(tmp_path, monkeypatch):
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    link = os.link
    def broken(source, target, **options):
        if str(target).endswith("service.csv"):
            raise OSError("simulated write failure")
        return link(source, target, **options)
    monkeypatch.setattr(os, "link", broken)
    result = inspect_synthetic_batch(sources, output, expected_sha256=digests)
    assert result["code"] == "OUTPUT_UNAVAILABLE"
    assert not list(output.iterdir())


def test_missing_input_and_missing_output_dir_are_safe(tmp_path):
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    assert inspect_synthetic_batch(sources, output, expected_sha256=digests)["ok"] is False
    output.mkdir()
    sources["VEHICLE"].unlink()
    result = inspect_synthetic_batch(sources, output, expected_sha256=digests)
    assert result["ok"] is False
    assert result["writes"] == 0
    assert not list(output.iterdir())


def test_batch_cli_maps_both_reordered_xlsx_sources(tmp_path, monkeypatch, capsys):
    import csv
    import json
    import sys
    from scripts.auto_synthetic_batch import main
    sources, _ = fixtures(tmp_path)
    mappings = {}
    specs = {
        "VEHICLE": (COLUMNS, ("VEHICLE", "SRC-1", str(uuid4()), str(uuid4()),
                              "1HGCM82633A004381", "SYNTHETIC", "TEST CAR", "DEMO")),
        "SERVICE": (FIELDS, ("SERVICE", "DEMO", "SRC-1",
                             "BRAKE-TEST", "SYNTHETIC", "30")),
    }
    for kind, (canonical, values) in specs.items():
        indexed = list(reversed(range(len(canonical))))
        workbook(sources[kind],
                 ["CUSTOM_" + str(i) for i in indexed],
                 [values[i] for i in indexed])
        mappings[kind] = [
            item for i, name in enumerate(canonical)
            for item in ("--" + kind.lower() + "-field", "CUSTOM_" + str(i) + "=" + name)
        ]
    digests = {kind: hashlib.sha256(source.read_bytes()).hexdigest()
               for kind, source in sources.items()}
    dest = tmp_path / "converted"
    dest.mkdir()
    argv = ["batch", "--synthetic-only",
            "--vehicle", str(sources["VEHICLE"]),
            "--vehicle-sha256", digests["VEHICLE"],
            "--service", str(sources["SERVICE"]),
            "--service-sha256", digests["SERVICE"],
            "--destination", str(dest),
            *mappings["VEHICLE"], *mappings["SERVICE"]]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["rows"] == {"VEHICLE": 1, "SERVICE": 1}
    assert result["writes"] == 0 and result["release_allowed"] is False
    for kind, (canonical, _) in specs.items():
        with (dest / (kind.lower() + ".csv")).open(encoding="utf-8", newline="") as handle:
            assert tuple(next(csv.reader(handle))) == canonical


def test_batch_cli_invalid_domain_mapping_denies_both_outputs(tmp_path, monkeypatch, capsys):
    import json
    import sys
    from scripts.auto_synthetic_batch import main
    sources, digests = fixtures(tmp_path)
    dest = tmp_path / "converted"
    dest.mkdir()
    monkeypatch.setattr(sys, "argv", [
        "batch", "--synthetic-only",
        "--vehicle", str(sources["VEHICLE"]),
        "--vehicle-sha256", digests["VEHICLE"],
        "--service", str(sources["SERVICE"]),
        "--service-sha256", digests["SERVICE"],
        "--destination", str(dest),
        "--service-field", "only=record_type",
    ])
    assert main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["writes"] == 0
    assert result["code"] == "MAPPING"
    assert not list(dest.iterdir())


def test_atomic_bundle_publishes_two_files_as_single_directory(tmp_path):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle
    sources, digests = fixtures(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    report = inspect_atomic_bundle(sources, output, expected_sha256=digests)
    assert report["ok"] and report["writes"] == 0 and not report["release_allowed"]
    assert report["mode"] == "SYNTHETIC_XLSX_ATOMIC_BUNDLE"
    bundle = output / "vehicle-service-bundle"
    assert sorted(path.name for path in bundle.iterdir()) == ["service.csv", "vehicle.csv"]
    for kind in sources:
        path = bundle / (kind.lower() + ".csv")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == report["sha256"][kind]
    assert not list(output.glob(".auto-bundle-*"))


def test_atomic_bundle_refuses_tampered_second_source_without_publishing(tmp_path):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    sources["SERVICE"].write_bytes(sources["SERVICE"].read_bytes() + b"corrupt")
    result = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert result["ok"] is False and result["code"] == "SOURCE_CHECKSUM"
    assert not list(destination.iterdir())


def test_atomic_bundle_refuses_existing_directory_even_when_empty(tmp_path):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    prior = destination / "vehicle-service-bundle"
    prior.mkdir()
    result = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert result["code"] == "OUTPUT_CONFLICT"
    assert list(prior.iterdir()) == []
    assert sorted(p.name for p in destination.iterdir()) == ["vehicle-service-bundle"]


def test_atomic_bundle_rename_failure_removes_unpublished_files(tmp_path, monkeypatch):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    def reject(*args, **kwargs):
        raise OSError("simulated rename failure")
    monkeypatch.setattr("scripts.auto_synthetic_batch._rename_directory_no_replace", reject)
    result = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert result["code"] == "OUTPUT_UNAVAILABLE"
    assert not list(destination.iterdir())


def test_atomic_bundle_concurrent_collision_is_never_overwritten(tmp_path, monkeypatch):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle, _rename_directory_no_replace
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    def racing_writer(source, target):
        target.mkdir()
        (target / "another-process").write_bytes(b"untouched")
        _rename_directory_no_replace(source, target)
    monkeypatch.setattr("scripts.auto_synthetic_batch._rename_directory_no_replace", racing_writer)
    report = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert report["ok"] is False and report["code"] == "OUTPUT_CONFLICT"
    assert (destination / "vehicle-service-bundle" / "another-process").read_bytes() == b"untouched"
    assert not list(destination.glob(".auto-bundle-*"))


def test_atomic_bundle_fails_closed_when_no_replace_primitive_missing(tmp_path, monkeypatch):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    monkeypatch.setattr("scripts.auto_synthetic_batch.sys.platform", "win32")
    report = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert report["code"] == "OUTPUT_UNAVAILABLE"
    assert not list(destination.iterdir())


def test_atomic_bundle_directory_sync_occurs_before_and_after_publish(tmp_path, monkeypatch):
    from scripts.auto_synthetic_batch import inspect_atomic_bundle, _sync_directory
    sources, digests = fixtures(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    sync_order = []
    def tracked_sync(path):
        sync_order.append(path.name)
        _sync_directory(path)
    monkeypatch.setattr("scripts.auto_synthetic_batch._sync_directory", tracked_sync)
    result = inspect_atomic_bundle(sources, destination, expected_sha256=digests)
    assert result["ok"]
    assert sync_order == ["vehicle-service-bundle", "output"]
