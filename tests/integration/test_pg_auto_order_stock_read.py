"""PG17 tenant isolation: work order warehouse read across organizations."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_order_stock_read import list_work_order_stock
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku, record
from test_pg_auto_stock_service import grant
from test_pg_auto_work_order_stock import work_order, act
from test_pg_baseline import DSN


def test_scoped_work_order_ledger_and_revocation():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other, otherloc, _ = _org_and_bay(db)
        order, _ = work_order(db, d, org, loc, bay)
        item = sku(db, org, loc)
        record(db, item, "RECEIPT", 5)
        role = grant(db, d["intake"], org, loc)
        act(db, d, org, loc, order, item, "RESERVE", 3)
        act(db, d, org, loc, order, item, "ISSUE", 2)
        token = d["tokens"][d["intake"]]
        events = list_work_order_stock(db, token, org, loc, order)
        assert {event["action"] for event in events} == {"RESERVE", "ISSUE"}
        assert {event["article"] for event in events} == {
            db.execute("SELECT article FROM auto_stock_sku WHERE id=%s", (item,)).fetchone()[0]
        }
        assert all(event["quantity"] > 0 for event in events)
        for o, l, wo in ((other, otherloc, order), (org, otherloc, order),
                         (org, loc, uuid4())):
            with pytest.raises(AutoStockDenied):
                list_work_order_stock(db, token, o, l, wo)
        assert list_work_order_stock(db, token, org, loc, order, limit=1) and len(
            list_work_order_stock(db, token, org, loc, order, limit=1)) == 1
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (role,))
        with pytest.raises(AutoStockDenied):
            list_work_order_stock(db, token, org, loc, order)


@pytest.mark.parametrize("limit", [0, 101, True, -1])
def test_invalid_limit_before_db(limit):
    with pytest.raises(AutoStockDenied, match="INVALID_INPUT"):
        list_work_order_stock(None, "test", uuid4(), uuid4(), uuid4(), limit=limit)
