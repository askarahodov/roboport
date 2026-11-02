"""Synthetic PG17 vehicle import staging; no customer ownership/repair history."""
import csv
import hashlib
from uuid import uuid4

import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay

from scripts.auto_vehicle_migration_preflight import COLUMNS
from scripts.auto_vehicle_migration_pg_staging import (
    VehicleStagingRejected, stage_synthetic_vehicles,
)


def source(path, records):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        writer.writerows(records)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def vehicle(org, loc, vin="1HGCM82633A004352", source_id="VEH-1"):
    return ("VEHICLE", source_id, str(org), str(loc), vin,
            "SYNTHETIC", "TEST CAR", "DEMO")


def test_vehicle_staging_exact_replay_and_no_live_vehicle_insert(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "vehicles.csv", uuid4()
        digest = source(path, [vehicle(org, loc)])
        first = stage_synthetic_vehicles(db, path, organization_id=org,
                                         batch_id=batch, expected_sha256=digest)
        second = stage_synthetic_vehicles(db, path, organization_id=org,
                                          batch_id=batch, expected_sha256=digest)
        assert first == {"ok": True, "replayed": False, "count": 1, "writes": 2,
                         "mode": "SYNTHETIC_VEHICLE_STAGING"}
        assert second["replayed"] and second["writes"] == 0
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle WHERE vin=%s",
            ("1HGCM82633A004352",),
        ).fetchone()[0] == 0
        assert db.execute("""
            SELECT count(*) FROM auto_vehicle_customer_link l
            JOIN auto_vehicle v ON v.id=l.vehicle_id WHERE v.vin=%s
        """, ("1HGCM82633A004352",)).fetchone()[0] == 0


def test_vehicle_staging_rollback_conflict_and_disabled_location(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch, path = uuid4(), tmp_path / "vehicles.csv"
        digest = source(path, [vehicle(org, loc)])
        with pytest.raises(RuntimeError):
            with db.transaction():
                stage_synthetic_vehicles(db, path, organization_id=org,
                                         batch_id=batch, expected_sha256=digest)
                raise RuntimeError("rollback")
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_batch WHERE id=%s",
            (batch,),
        ).fetchone()[0] == 0
        stage_synthetic_vehicles(db, path, organization_id=org,
                                 batch_id=batch, expected_sha256=digest)
        altered = source(path, [vehicle(org, loc, vin="JH4KA7561PC008269")])
        with pytest.raises(VehicleStagingRejected, match="batch mismatch"):
            stage_synthetic_vehicles(db, path, organization_id=org,
                                     batch_id=batch, expected_sha256=altered)
        db.execute("UPDATE auto_service_location SET disabled_at=now() WHERE id=%s", (loc,))
        with pytest.raises(VehicleStagingRejected, match="scope unavailable"):
            stage_synthetic_vehicles(db, path, organization_id=org,
                                     batch_id=batch, expected_sha256=altered)


def test_vehicle_staging_rejects_mixed_tenants_without_writes(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        other, other_loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "mixed.csv", uuid4()
        digest = source(path, [
            vehicle(org, loc), vehicle(other, other_loc, "JH4KA7561PC008269", "VEH-2")
        ])
        with pytest.raises(VehicleStagingRejected, match="tenant mismatch"):
            stage_synthetic_vehicles(db, path, organization_id=org,
                                     batch_id=batch, expected_sha256=digest)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_batch WHERE id=%s",
            (batch,),
        ).fetchone()[0] == 0


def test_existing_live_vehicle_vin_blocks_staging_without_writes(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "vehicles.csv", uuid4()
        digest = source(path, [vehicle(org, loc, vin="1HGCM82633A004353")])
        existing_vehicle = uuid4()
        db.execute(
            "INSERT INTO auto_vehicle(id,vin,make,model) VALUES (%s,%s,%s,%s)",
            (existing_vehicle, "1HGCM82633A004353", "SYNTHETIC", "ALREADY EXISTS"),
        )
        with pytest.raises(VehicleStagingRejected, match="vehicle identity conflicts") as error:
            stage_synthetic_vehicles(db, path, organization_id=org,
                                     batch_id=batch, expected_sha256=digest)
        assert "1HGCM82633A004353" not in str(error.value)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_batch WHERE id=%s",
            (batch,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT model FROM auto_vehicle WHERE id=%s",
            (existing_vehicle,),
        ).fetchone()[0] == "ALREADY EXISTS"


def test_replay_detects_new_conflicting_live_vehicle_without_mutations(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "vehicles.csv", uuid4()
        digest = source(path, [vehicle(org, loc)])
        stage_synthetic_vehicles(db, path, organization_id=org,
                                 batch_id=batch, expected_sha256=digest)
        db.execute(
            "INSERT INTO auto_vehicle(id,vin,make,model) VALUES (%s,%s,%s,%s)",
            (uuid4(), "1HGCM82633A004352", "SYNTHETIC", "NEW CONFLICT"),
        )
        with pytest.raises(VehicleStagingRejected, match="vehicle identity conflicts"):
            stage_synthetic_vehicles(db, path, organization_id=org,
                                     batch_id=batch, expected_sha256=digest)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_line WHERE batch_id=%s",
            (batch,),
        ).fetchone()[0] == 1
