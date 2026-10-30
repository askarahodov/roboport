"""PG17 and HTTP checks for dual-role, no-partial-response order overview."""
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_order_overview import get_order_overview
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_baseline import DSN
from test_pg_auto_order_customer_read import prepare
from test_pg_auto_stock_ledger import sku, record
from test_pg_auto_stock_service import grant
from test_pg_auto_work_order_stock import act


def test_overview_requires_both_grants_and_rechecks_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, foreign, foreign_loc = prepare()
    token = d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        item = sku(db, org, loc)
        record(db, item, "RECEIPT", 3)
        role = grant(db, d["intake"], org, loc)
        act(db, d, org, loc, order, item, "RESERVE", 1)

    client = TestClient(app, base_url="https://127.0.0.1")
    url = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/overview"
    assert client.get(url).status_code == 403
    client.cookies.set("rp_session", token)
    ok = client.get(url)
    assert ok.status_code == 200, ok.text
    assert ok.headers["cache-control"] == "no-store"
    assert ok.json()["customer"]["orderId"] == str(order)
    assert len(ok.json()["stock"]["items"]) == 1
    assert client.get(url + "?limit=101").status_code == 422
    assert client.get(url.replace(str(org), str(foreign)).replace(str(loc), str(foreign_loc))).status_code == 403
    assert client.get(url.replace(str(order), str(uuid4()))).status_code == 403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (role,))
    assert client.get(url).status_code == 403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(url).status_code == 404


def test_overview_denies_warehouse_only_and_revoked_proof(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, _, _ = prepare()
    with psycopg.connect(DSN) as db:
        role = grant(db, d["intake"], org, loc)
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s AND user_id=%s
                        AND role_code='INTAKE' AND revoked_at IS NULL""",
                   (org, loc, d["intake"]))
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    url = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/overview"
    assert client.get(url).status_code == 403


@pytest.mark.parametrize("token,limit,expected", [(" ", 1, "FORBIDDEN"), ("token", 0, "INVALID_INPUT"), ("token", True, "INVALID_INPUT")])
def test_invalid_input_before_db(token, limit, expected):
    with pytest.raises(AutoStockDenied, match=expected):
        get_order_overview(None, token, uuid4(), uuid4(), uuid4(), limit=limit)


def test_overview_pagination_tenant_binding_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, foreign, foreign_loc = prepare()
    with psycopg.connect(DSN) as db:
        item = sku(db, org, loc)
        record(db, item, "RECEIPT", 10)
        role = grant(db, d["intake"], org, loc)
        act(db, d, org, loc, order, item, "RESERVE", 4)
        act(db, d, org, loc, order, item, "ISSUE", 1)
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    path = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/overview"
    first = client.get(path, params={"limit": 1})
    assert first.status_code == 200, first.text
    first_page = first.json()["stock"]
    assert len(first_page["items"]) == 1
    assert first_page["nextCursor"]
    cursor = first_page["nextCursor"]
    second = client.get(path, params={"limit": 1, "cursor": cursor})
    assert second.status_code == 200, second.text
    second_page = second.json()["stock"]
    assert len(second_page["items"]) == 1
    assert second_page["nextCursor"] is None
    assert second_page["items"][0]["eventId"] != first_page["items"][0]["eventId"]
    assert client.get(path, params={"cursor": cursor[:-1] + ("A" if cursor[-1] != "A" else "B")}).status_code == 422
    assert client.get(path.replace(str(order), str(uuid4())), params={"cursor": cursor}).status_code == 422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (role,))
    assert client.get(path, params={"cursor": cursor}).status_code == 403


def test_overview_rejects_invalid_cursor_before_db():
    with pytest.raises(AutoStockDenied, match="INVALID_INPUT"):
        get_order_overview(None, "session", uuid4(), uuid4(), uuid4(), cursor="not-a-signed-cursor")
