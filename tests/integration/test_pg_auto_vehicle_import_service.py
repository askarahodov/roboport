"""PG17: live MANAGER session + branch grant + revocation for staging."""
import csv
import hashlib
from uuid import uuid4

import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant

from roboport_api.auto_vehicle_import_service import (
    VehicleImportDenied, stage_authorized_synthetic_vehicles,
)
from roboport_api.session_tokens import token_digest
from scripts.auto_vehicle_migration_preflight import COLUMNS
from scripts.auto_vehicle_migration_pg_staging import VehicleStagingRejected


def fixture(path, org, loc):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(COLUMNS)
        writer.writerow(("VEHICLE", "TEST-1", str(org), str(loc),
                         "1HGCM82633A004352", "SYNTHETIC", "TEST CAR", "DEMO"))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(db, token, path, org, loc, digest, batch=None):
    return stage_authorized_synthetic_vehicles(
        db, token, path, organization_id=org, location_id=loc,
        batch_id=batch or uuid4(), expected_sha256=digest,
    )


def test_scoped_manager_grant_replay_and_revocation(tmp_path):
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path = tmp_path / "vehicles.csv"
        digest = fixture(path, org, loc)
        token = d["tokens"][d["intake"]]
        with pytest.raises(VehicleImportDenied):
            stage(db, token, path, org, loc, digest)
        key = grant(db, d["intake"], org, loc, "MANAGER")
        batch = uuid4()
        assert stage(db, token, path, org, loc, digest, batch)["writes"] == 2
        assert stage(db, token, path, org, loc, digest, batch)["writes"] == 0
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (key,))
        with pytest.raises(VehicleImportDenied):
            stage(db, token, path, org, loc, digest, batch)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_line WHERE batch_id=%s",
            (batch,),
        ).fetchone()[0] == 1


def test_cross_branch_and_wrong_role_fail_closed(tmp_path):
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        other = uuid4()
        db.execute(
            "INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
            (other, "LOC_" + other.hex[:10].upper(), org),
        )
        token = d["tokens"][d["intake"]]
        grant(db, d["intake"], org, loc, "WAREHOUSE")
        path = tmp_path / "vehicles.csv"
        digest = fixture(path, org, loc)
        with pytest.raises(VehicleImportDenied):
            stage(db, token, path, org, loc, digest)
        grant(db, d["intake"], org, loc, "MANAGER")
        with pytest.raises(VehicleImportDenied):
            stage(db, token, path, org, other, digest)
        with pytest.raises(VehicleStagingRejected, match="branch mismatch"):
            stage(db, token, path, org, loc, fixture(path, org, other))
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_batch WHERE organization_id=%s",
            (org,),
        ).fetchone()[0] == 0


def test_revoked_session_denies_authorized_stage(tmp_path):
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        token = d["tokens"][d["intake"]]
        grant(db, d["intake"], org, loc, "MANAGER")
        path = tmp_path / "vehicles.csv"
        digest = fixture(path, org, loc)
        db.execute("UPDATE user_session SET revoked_at=now() WHERE token_hash=%s",
                   (token_digest(token),))
        with pytest.raises(VehicleImportDenied):
            stage(db, token, path, org, loc, digest)
