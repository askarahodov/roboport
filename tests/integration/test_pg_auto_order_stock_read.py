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


def test_http_order_stock_route_requires_session_and_exact_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other, foreign_loc, _ = _org_and_bay(db)
        order, _ = work_order(db, d, org, loc, bay)
        item = sku(db, org, loc)
        record(db, item, "RECEIPT", 3)
        grant(db, d["intake"], org, loc)
        act(db, d, org, loc, order, item, "RESERVE", 2)
    client = TestClient(app, base_url="https://127.0.0.1")
    path = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/stock"
    assert client.get(path).status_code == 403
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    allowed = client.get(path)
    assert allowed.status_code == 200, allowed.text
    assert allowed.headers["cache-control"] == "no-store"
    assert len(allowed.json()["items"]) == 1
    assert allowed.json()["items"][0]["action"] == "RESERVE"
    for invalid in (f"/api/v1/dev/auto/organizations/{other}/locations/{foreign_loc}/orders/{order}/stock",
                    f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{uuid4()}/stock"):
        assert client.get(invalid).status_code == 403
    assert client.get(path + "?limit=101").status_code == 422
    assert client.get(path + "?limit=1").status_code == 200
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(path).status_code == 404


def test_stock_read_rejects_whitespace_token_without_db():
    with pytest.raises(AutoStockDenied, match="FORBIDDEN"):
        list_work_order_stock(None, "  ", uuid4(), uuid4(), uuid4())


def test_http_order_stock_denies_revoked_warehouse_grant(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app

    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        order, _ = work_order(db, d, org, loc, bay)
        item = sku(db, org, loc)
        record(db, item, "RECEIPT", 2)
        role = grant(db, d["intake"], org, loc)
        act(db, d, org, loc, order, item, "RESERVE", 1)

    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    path = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/stock"
    initial = client.get(path)
    assert initial.status_code == 200, initial.text
    assert initial.json()["items"][0]["action"] == "RESERVE"

    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
            (role,),
        )
    assert client.get(path).status_code == 403
    assert client.get(path + "?limit=1").status_code == 403
