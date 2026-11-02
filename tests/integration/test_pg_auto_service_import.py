"""PG17 contract: synthetic services cannot become real work orders or PII."""
import csv
import hashlib
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_service_import import (
    FIELDS, ServiceImportDenied, inspect_synthetic_services, stage_synthetic_services,
)
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def write(path, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def record(source="SRV-1", code="OIL-CHANGE", duration="30"):
    return ("SERVICE", "DEMO", source, code, "SYNTHETIC OIL CHANGE", duration)


def stage(db, token, path, org, loc, batch, digest):
    return stage_synthetic_services(db, token, path, organization_id=org,
                                    location_id=loc, batch_id=batch,
                                    expected_sha256=digest)


def test_service_stage_requires_manager_and_replay_is_exact(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "services.csv", uuid4()
        digest = write(path, [record()])
        token = scenario["tokens"][scenario["intake"]]
        assert inspect_synthetic_services(path, expected_sha256=digest)["count"] == 1
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            stage(db, token, path, org, loc, batch, digest)
        grant_id = grant(db, scenario["intake"], org, loc, "MANAGER")
        assert stage(db, token, path, org, loc, batch, digest)["writes"] == 2
        assert stage(db, token, path, org, loc, batch, digest)["writes"] == 0
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_service_import_line SET code='TAMPER' WHERE batch_id=%s",
                           (batch,))
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            stage(db, token, path, org, loc, batch, digest)
        assert db.execute("SELECT count(*) FROM auto_service_import_line WHERE batch_id=%s",
                          (batch,)).fetchone()[0] == 1


def test_service_stage_rejects_foreign_branch_and_batch_collision(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        foreign, other_loc, _ = _org_and_bay(db)
        token = scenario["tokens"][scenario["intake"]]
        grant(db, scenario["intake"], org, loc, "MANAGER")
        path, batch = tmp_path / "services.csv", uuid4()
        digest = write(path, [record()])
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            stage(db, token, path, foreign, other_loc, batch, digest)
        stage(db, token, path, org, loc, batch, digest)
        altered = write(path, [record(code="BRAKE-CHECK")])
        with pytest.raises(ServiceImportDenied, match="BATCH_CONFLICT"):
            stage(db, token, path, org, loc, batch, altered)
        assert db.execute(
            "SELECT count(*) FROM auto_service_import_batch WHERE id=%s",
            (batch,),
        ).fetchone()[0] == 1


def test_service_preflight_rejects_source_duplicates_and_formula_injection(tmp_path):
    path = tmp_path / "invalid.csv"
    digest = write(path, [record(), record("SRV-2")])
    assert inspect_synthetic_services(path, expected_sha256=digest)["code"] == "DUPLICATE"
    digest = write(path, [("SERVICE", "DEMO", "SRV-1", "OIL-CHANGE", "=cmd", "30")])
    assert inspect_synthetic_services(path, expected_sha256=digest)["code"] == "TITLE"
    assert inspect_synthetic_services(path, expected_sha256="0"*64)["ok"] is False


def test_service_staging_rollback(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        token = scenario["tokens"][scenario["intake"]]
        grant(db, scenario["intake"], org, loc, "MANAGER")
        batch, path = uuid4(), tmp_path / "services.csv"
        digest = write(path, [record()])
        with pytest.raises(RuntimeError):
            with db.transaction():
                stage(db, token, path, org, loc, batch, digest)
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_service_import_batch WHERE id=%s",
                          (batch,)).fetchone()[0] == 0
