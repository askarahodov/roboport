"""PG17 integration of scoped WAREHOUSE authority and immutable stock ledger."""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied, mutate_auto_stock
from roboport_api.session_tokens import token_digest
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku, balance
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def grant(db, user, org, loc, role="WAREHOUSE"):
    key = uuid4()
    db.execute(
        """INSERT INTO auto_organization_role_grant
           (id,organization_id,location_id,user_id,role_code)
           VALUES (%s,%s,%s,%s,%s)""",
        (key, org, loc, user, role),
    )
    return key


def action(db, d, actor, org, loc, item, operation="RECEIPT", count=3, key=None):
    return mutate_auto_stock(
        db, d["tokens"][actor], org, loc, item, key or uuid4(), operation, count,
    )


def test_warehouse_scope_role_idempotency_and_revoke():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        foreign, other_loc, _ = _org_and_bay(db)
        item = sku(db, org, loc)
        other = sku(db, foreign, other_loc)
        grant_id = grant(db, d["intake"], org, loc)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["manager"], org, loc, item)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], foreign, other_loc, other)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], foreign, other_loc, item)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], org, loc, other)
        key = uuid4()
        eid = action(db, d, d["intake"], org, loc, item, key=key)
        assert action(db, d, d["intake"], org, loc, item, key=key) == eid
        assert balance(db, item) == (3, 0)
        action(db, d, d["intake"], org, loc, item, "RESERVE", 2)
        with pytest.raises(AutoStockConflict):
            action(db, d, d["intake"], org, loc, item, "RESERVE", 2)
        with pytest.raises(AutoStockConflict):
            action(db, d, d["intake"], org, loc, item, "RECEIPT", 4, key)
        assert balance(db, item) == (3, 2)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], org, loc, item, key=key)
        assert balance(db, item) == (3, 2)


def test_branch_scoping_and_revoked_session():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        second = uuid4()
        db.execute(
            "INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
            (second, "LOC_" + second.hex[:10].upper(), org),
        )
        here, there = sku(db, org, loc), sku(db, org, second)
        grant(db, d["intake"], org, loc)
        action(db, d, d["intake"], org, loc, here)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], org, second, there)
        db.execute("UPDATE user_session SET revoked_at=now() WHERE token_hash=%s",
                   (token_digest(d["tokens"][d["intake"]]),))
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], org, loc, here)
        assert balance(db, here) == (3, 0)


def test_mutation_locks_grant_against_concurrent_revocation():
    d = _scenario()
    with psycopg.connect(DSN) as setup:
        org, loc, _ = _org_and_bay(setup)
        item = sku(setup, org, loc)
        grant_id = grant(setup, d["intake"], org, loc)
    with psycopg.connect(DSN) as checking, psycopg.connect(DSN) as revoking:
        with checking.transaction():
            action(checking, d, d["intake"], org, loc, item)
            revoking.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with revoking.transaction():
                    revoking.execute(
                        "UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                        (grant_id,),
                    )
        revoking.execute(
            "UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
            (grant_id,),
        )
        # Commit the competing revocation before checking the next mutation:
        # otherwise its row-level UPDATE lock stays open and deadlocks this
        # sequential test against the other connection.
        revoking.commit()
        with pytest.raises(AutoStockDenied):
            action(checking, d, d["intake"], org, loc, item)


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, 1_000_000_001])
def test_invalid_quantity_rejected_before_sql(quantity):
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        item = sku(db, org, loc)
        grant(db, d["intake"], org, loc)
        with pytest.raises(AutoStockDenied):
            action(db, d, d["intake"], org, loc, item, count=quantity)
        assert balance(db, item) == (0, 0)
