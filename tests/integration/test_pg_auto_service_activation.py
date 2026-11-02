"""PG17 synthetic service catalog activation end-to-end; no customer/finance linkage."""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_service_import import ServiceImportDenied
from roboport_api.auto_service_activation import activate_synthetic_services
from test_pg_auto_service_import import write, record, stage
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def activate(db, token, org, loc, batch, enabled=True):
    return activate_synthetic_services(
        db, token, organization_id=org, location_id=loc,
        batch_id=batch, synthetic_only=enabled,
    )


def test_service_catalog_activation_authority_replay_and_no_workorder(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        path, batch = tmp_path / "services.csv", uuid4()
        digest = write(path, [record(code="OIL-CAT-01")])
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            activate(db, token, org, loc, batch)
        grant_id = grant(db, actor, org, loc, "MANAGER")
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            activate(db, token, org, loc, batch, enabled=False)
        stage(db, token, path, org, loc, batch, digest)
        result = activate(db, token, org, loc, batch)
        assert result["count"] == 1 and result["writes"] == 2
        assert not result["replayed"]
        assert activate(db, token, org, loc, batch)["writes"] == 0
        row = db.execute(
            "SELECT code,title,duration_minutes FROM auto_service_catalog WHERE location_id=%s AND code=%s",
            (loc, "OIL-CAT-01"),
        ).fetchone()
        assert row == ("OIL-CAT-01", "SYNTHETIC OIL CHANGE", 30)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_service_catalog SET code='TAMPER' WHERE location_id=%s AND code=%s",
                           (loc, "OIL-CAT-01"))
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
        with pytest.raises(ServiceImportDenied, match="FORBIDDEN"):
            activate(db, token, org, loc, batch)


def test_activation_rejects_foreign_branch_and_existing_code(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        _, other_loc, _ = _org_and_bay(db)
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        grant(db, actor, org, loc, "MANAGER")
        path, first, second = tmp_path / "services.csv", uuid4(), uuid4()
        digest = write(path, [record(code="BRAKE-CAT-02")])
        stage(db, token, path, org, loc, first, digest)
        with pytest.raises(ServiceImportDenied):
            activate(db, token, org, other_loc, first)
        activate(db, token, org, loc, first)
        stage(db, token, path, org, loc, second, digest)
        with pytest.raises(ServiceImportDenied, match="SERVICE_CONFLICT"):
            activate(db, token, org, loc, second)
        assert db.execute(
            "SELECT count(*) FROM auto_service_import_activation WHERE batch_id=%s",
            (second,),
        ).fetchone()[0] == 0


def test_database_rejects_forged_catalog_lineage(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        grant(db, actor, org, loc, "MANAGER")
        path, batch = tmp_path / "services.csv", uuid4()
        digest = write(path, [record(code="INSPECT-CAT-03")])
        stage(db, token, path, org, loc, batch, digest)
        fake = uuid4()
        db.execute("""
            INSERT INTO auto_service_catalog(
              id,organization_id,location_id,code,title,duration_minutes)
            VALUES(%s,%s,%s,'FORGED-CAT','UNRELATED',30)
        """, (fake, org, loc))
        with pytest.raises(psycopg.errors.RaiseException, match="lineage mismatch"):
            with db.transaction():
                db.execute("""
                    INSERT INTO auto_service_import_activation(batch_id,row_number,service_id)
                    VALUES(%s,2,%s)
                """, (batch, fake))
        assert activate(db, token, org, loc, batch)["count"] == 1


def test_activation_rollback_is_atomic(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        grant(db, actor, org, loc, "MANAGER")
        path, batch = tmp_path / "services.csv", uuid4()
        digest = write(path, [record(code="ROLL-CAT-04")])
        stage(db, token, path, org, loc, batch, digest)
        with pytest.raises(RuntimeError):
            with db.transaction():
                activate(db, token, org, loc, batch)
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_service_import_activation WHERE batch_id=%s",
                          (batch,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM auto_service_catalog WHERE code=%s",
                          ("ROLL-CAT-04",)).fetchone()[0] == 0
