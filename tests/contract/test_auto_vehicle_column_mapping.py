"""Contract tests for explicit, redacted and no-overwrite vehicle CSV mapping."""
import csv
import hashlib
from uuid import uuid4

import pytest

from scripts.auto_vehicle_column_mapping import (
    VehicleMappingRejected, map_synthetic_vehicle_csv,
)
from scripts.auto_vehicle_migration_preflight import COLUMNS, inspect_vehicle_csv


MAPPING = {
    "тип": "record_type", "внешний_id": "source_id",
    "организация": "organization_id", "филиал": "location_id",
    "номер": "vin", "марка": "make", "модель": "model",
    "источник": "source_system",
}


def write_fixture(path, headers, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerows(rows)


def values(org, loc):
    return ("VEHICLE", "TEST-1", str(org), str(loc),
            "1HGCM82633A004371", "SYNTHETIC", "TEST CAR", "DEMO")


def test_explicit_mapping_produces_canonical_staging_input(tmp_path):
    org, loc = uuid4(), uuid4()
    source, target = tmp_path / "input.csv", tmp_path / "canonical.csv"
    reversed_mapping = list(reversed(list(MAPPING)))
    original = dict(zip(MAPPING, values(org, loc)))
    write_fixture(source, reversed_mapping, [[original[h] for h in reversed_mapping]])
    report = map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert report["ok"] and report["count"] == 1 and report["writes"] == 0
    assert report["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert inspect_vehicle_csv(target)["ok"]
    assert tuple(next(csv.reader(target.open(encoding="utf-8")))) == COLUMNS
    assert "1HGCM82633A004371" not in str(report)
    assert str(org) not in str(report)
    assert target.stat().st_mode & 0o777 == 0o600
    with pytest.raises(VehicleMappingRejected, match="OUTPUT_UNAVAILABLE"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)


def test_rejects_extra_owner_column_and_ambiguous_header_without_output(tmp_path):
    org, loc = uuid4(), uuid4()
    source, target = tmp_path / "owner.csv", tmp_path / "canonical.csv"
    write_fixture(source, [*MAPPING, "владелец"], [(*values(org, loc), "person")])
    with pytest.raises(VehicleMappingRejected, match="SCHEMA"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert not target.exists()
    write_fixture(source, ["тип", "тип", *list(MAPPING)[2:]],
                  [values(org, loc)])
    with pytest.raises(VehicleMappingRejected, match="SCHEMA"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert not target.exists()


def test_rejects_malformed_vin_and_duplicate_vin_without_output(tmp_path):
    org, loc = uuid4(), uuid4()
    source, target = tmp_path / "bad.csv", tmp_path / "canonical.csv"
    bad = list(values(org, loc))
    bad[4] = "=1+1"
    write_fixture(source, MAPPING, [bad])
    with pytest.raises(VehicleMappingRejected, match="PREFLIGHT"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert not target.exists()
    second = list(values(org, loc))
    second[1] = "TEST-2"
    write_fixture(source, MAPPING, [values(org, loc), second])
    with pytest.raises(VehicleMappingRejected, match="PREFLIGHT"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert not target.exists()


def test_rejects_symlink_source_and_output(tmp_path):
    org, loc = uuid4(), uuid4()
    source, target = tmp_path / "input.csv", tmp_path / "output.csv"
    write_fixture(source, MAPPING, [values(org, loc)])
    link = tmp_path / "link.csv"
    link.symlink_to(source)
    with pytest.raises(VehicleMappingRejected, match="UNAVAILABLE"):
        map_synthetic_vehicle_csv(link, target, mapping=MAPPING)
    assert not target.exists()
    target.symlink_to(source)
    before = source.read_bytes()
    with pytest.raises(VehicleMappingRejected, match="OUTPUT_UNAVAILABLE"):
        map_synthetic_vehicle_csv(source, target, mapping=MAPPING)
    assert source.read_bytes() == before


def test_invalid_mapping_is_rejected_without_identifiers(tmp_path):
    source, target = tmp_path / "input.csv", tmp_path / "output.csv"
    with pytest.raises(VehicleMappingRejected, match="MAPPING"):
        map_synthetic_vehicle_csv(source, target, mapping={"номер": "vin"})
    assert not target.exists()
