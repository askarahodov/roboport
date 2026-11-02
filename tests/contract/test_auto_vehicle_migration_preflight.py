"""Contract tests: synthetic vehicles must never import owner or history."""
import csv
import json
import sys
from uuid import uuid4

import pytest

from scripts.auto_vehicle_migration_preflight import (
    COLUMNS, inspect_vehicle_csv, main
)


def write(path, rows):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(rows)


def row(org, loc, *, source="VEH-1", vin="1HGCM82633A004352",
        make="SYNTHETIC", model="TEST CAR", provenance="DEMO"):
    return ("VEHICLE", source, str(org), str(loc), vin, make, model, provenance)


def test_synthetic_vehicle_valid_and_repeat_without_vin_leaks(tmp_path):
    org, loc = uuid4(), uuid4()
    path = tmp_path / "synthetic-vehicle.csv"
    write(path, [row(org, loc)])
    expected = {"ok": True, "counts": {"VEHICLE": 1}, "errors": [],
                "mode": "SYNTHETIC_VEHICLE_DRY_RUN", "writes": 0}
    assert inspect_vehicle_csv(path) == expected
    assert inspect_vehicle_csv(path) == expected
    assert "1HGCM82633A004352" not in str(expected)
    assert str(org) not in str(expected)


def test_duplicate_source_and_duplicate_vin_are_rejected(tmp_path):
    org, loc = uuid4(), uuid4()
    path = tmp_path / "duplicate.csv"
    write(path, [
        row(org, loc),
        row(org, loc, vin="JH4KA7561PC008269"),
        row(org, loc, source="VEH-2"),
    ])
    result = inspect_vehicle_csv(path)
    assert {error["code"] for error in result["errors"]} == {
        "DUPLICATE_SOURCE", "DUPLICATE_VIN"
    }
    assert result["counts"] == {"VEHICLE": 1}
    assert "JH4KA7561PC008269" not in str(result)


def test_vin_and_provenance_rejected_without_identifiers_in_report(tmp_path):
    org, loc = uuid4(), uuid4()
    path = tmp_path / "invalid.csv"
    write(path, [
        row(org, loc, vin="1HGCM82633I004352"),
        row(org, loc, source="VEH-2", provenance=""),
        row(org, loc, source="VEH-3", make="=FORMULA"),
    ])
    result = inspect_vehicle_csv(path)
    assert {error["code"] for error in result["errors"]} == {
        "VIN_FORMAT", "PROVENANCE", "LABEL"
    }
    assert str(org) not in str(result) and "=FORMULA" not in str(result)


def test_owner_history_columns_never_accepted(tmp_path):
    org, loc = uuid4(), uuid4()
    path = tmp_path / "owner.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow((*COLUMNS, "customer_id", "repair_history"))
        writer.writerow((*row(org, loc), "person", "prior repair"))
    assert inspect_vehicle_csv(path)["errors"] == [{"row": 1, "code": "SCHEMA"}]


def test_empty_symlink_and_cli(tmp_path, monkeypatch, capsys):
    org, loc = uuid4(), uuid4()
    path = tmp_path / "empty.csv"
    write(path, [])
    assert inspect_vehicle_csv(path)["errors"] == [{"row": 0, "code": "EMPTY_INPUT"}]
    link = tmp_path / "source-link.csv"
    link.symlink_to(path)
    assert inspect_vehicle_csv(link)["ok"] is False
    write(path, [row(org, loc)])
    monkeypatch.setattr(sys, "argv", ["vehicle-preflight", "--synthetic-only", str(path)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"VEHICLE": 1}
    monkeypatch.setattr(sys, "argv", ["vehicle-preflight", str(path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
