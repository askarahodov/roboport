"""PG17 + HTTP: customer identity is scoped to an active verified order link."""
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_order_customer_read import get_order_customer_summary
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_work_order_stock import work_order


def prepare():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        foreign, foreign_loc, _ = _org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"], d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES (%s,%s,%s,%s,'INTAKE')""", (uuid4(),org,loc,d["intake"]))
        order, _ = work_order(db, d, org, loc, bay)
    return d, org, loc, order, foreign, foreign_loc


def test_verified_customer_order_scope_and_revoke():
    d,org,loc,order,foreign,foreign_loc = prepare()
    token=d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        result=get_order_customer_summary(db,token,org,loc,order)
        assert result["orderId"] == str(order)
        assert result["customerId"] == str(db.execute("SELECT customer_id FROM auto_vehicle_customer_link WHERE id=%s", (d["link"],)).fetchone()[0])
        assert result["vehicleId"] == str(d["vehicle"])
        assert result["customerLinkId"] == str(d["link"])
        for a,b,c in ((foreign,foreign_loc,order),(org,foreign_loc,order),(org,loc,uuid4())):
            with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
                get_order_customer_summary(db,token,a,b,c)
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            get_order_customer_summary(db,d["tokens"][d["manager"]],org,loc,order)
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s", (d["link"],))
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            get_order_customer_summary(db,token,org,loc,order)


def test_http_read_rejects_missing_session_foreign_scope_and_revoked_link(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,foreign,foreign_loc=prepare()
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/customer"
    assert client.get(url).status_code == 403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    res=client.get(url)
    assert res.status_code == 200,res.text
    assert res.headers["cache-control"] == "no-store"
    with psycopg.connect(DSN) as check:
        actual_customer = check.execute("SELECT customer_id FROM auto_vehicle_customer_link WHERE id=%s", (d["link"],)).fetchone()[0]
    assert res.json()["customerId"] == str(actual_customer)
    assert client.get(url.replace(str(org),str(foreign)).replace(str(loc),str(foreign_loc))).status_code == 403
    assert client.get(url.replace(str(order),str(uuid4()))).status_code == 403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert client.get(url).status_code == 403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(url).status_code == 404


def test_invalid_scope_denied_before_database():
    with pytest.raises(AutoStockDenied,match="INVALID_INPUT"):
        get_order_customer_summary(None,"test",uuid4(),uuid4(),"wrong")
    with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
        get_order_customer_summary(None,"",uuid4(),uuid4(),uuid4())


def test_customer_read_rejects_whitespace_token_without_db():
    with pytest.raises(AutoStockDenied, match="FORBIDDEN"):
        get_order_customer_summary(None, "   ", uuid4(), uuid4(), uuid4())


def test_http_customer_read_denies_revoked_staff_grant(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, _, _ = prepare()
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    path = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/customer"
    assert client.get(path).status_code == 200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s AND user_id=%s
                        AND role_code='INTAKE' AND revoked_at IS NULL""",
                   (org, loc, d["intake"]))
    assert client.get(path).status_code == 403
