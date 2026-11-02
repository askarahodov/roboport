"""PG17 checks for explicit synthetic vehicle-only promotion and consent boundary."""
import csv
import hashlib
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from roboport_api.auto_vehicle_promotion_service import (
    VehiclePromotionDenied, promote_synthetic_vehicles,
)
from scripts.auto_vehicle_migration_preflight import COLUMNS
from scripts.auto_vehicle_migration_pg_staging import stage_synthetic_vehicles


def setup_batch(db, tmp_path, org, loc, vin):
    path = tmp_path / "promotion.csv"
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        writer.writerow(("VEHICLE", "PROMO-1", str(org), str(loc), vin,
                         "SYNTHETIC", "TEST CAR", "DEMO"))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    batch = uuid4()
    stage_synthetic_vehicles(db, path, organization_id=org, batch_id=batch,
                             expected_sha256=digest)
    return batch


def promote(db, token, org, loc, batch, enabled=True):
    return promote_synthetic_vehicles(
        db, token, organization_id=org, location_id=loc,
        batch_id=batch, synthetic_only=enabled,
    )


def test_authorized_vehicle_only_promotion_and_exact_replay(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = setup_batch(db, tmp_path, org, loc, "1HGCM82633A004361")
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        with pytest.raises(VehiclePromotionDenied, match="FORBIDDEN"):
            promote(db, token, org, loc, batch)
        grant(db, actor, org, loc, "MANAGER")
        with pytest.raises(VehiclePromotionDenied, match="FORBIDDEN"):
            promote(db, token, org, loc, batch, enabled=False)
        first = promote(db, token, org, loc, batch)
        assert first["count"] == 1 and first["writes"] == 2 and not first["replayed"]
        again = promote(db, token, org, loc, batch)
        assert again["replayed"] and again["writes"] == 0
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle WHERE vin=%s",
            ("1HGCM82633A004361",),
        ).fetchone()[0] == 1
        assert db.execute("""
            SELECT count(*) FROM auto_vehicle_customer_link l
            JOIN auto_vehicle v ON v.id=l.vehicle_id
            WHERE v.vin=%s
        """, ("1HGCM82633A004361",)).fetchone()[0] == 0


def test_promotion_denies_wrong_branch_and_revoked_grant(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        _, other, _ = _org_and_bay(db)
        batch = setup_batch(db, tmp_path, org, loc, "1HGCM82633A004362")
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        key = grant(db, actor, org, loc, "MANAGER")
        with pytest.raises(VehiclePromotionDenied):
            promote(db, token, org, other, batch)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (key,))
        with pytest.raises(VehiclePromotionDenied, match="FORBIDDEN"):
            promote(db, token, org, loc, batch)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle WHERE vin=%s",
            ("1HGCM82633A004362",),
        ).fetchone()[0] == 0


def test_promotion_transaction_rollback_includes_lineage(tmp_path):
    scenario = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = setup_batch(db, tmp_path, org, loc, "1HGCM82633A004363")
        actor = scenario["intake"]
        token = scenario["tokens"][actor]
        grant(db, actor, org, loc, "MANAGER")
        with pytest.raises(RuntimeError):
            with db.transaction():
                promote(db, token, org, loc, batch)
                raise RuntimeError("rollback synthetic fixture")
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_migration_promotion WHERE batch_id=%s",
            (batch,),
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle WHERE vin=%s",
            ("1HGCM82633A004363",),
        ).fetchone()[0] == 0
