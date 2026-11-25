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


def test_http_customer_read_denies_disabled_organization(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, _, _ = prepare()
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    path = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/customer"
    assert client.get(path).status_code == 200
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization SET disabled_at=now() WHERE id=%s", (org,))
    assert client.get(path).status_code == 403


def test_dual_role_intake_projections_respect_mechanic_read_deny_and_cutoff(monkeypatch):
    from test_pg_auto_document_access_http import admin, set_rule, url
    from test_pg_auto_work_order_estimate_http import token

    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, _, _ = prepare()
    subject = d["intake"]
    with psycopg.connect(DSN) as db:
        admin(db, org, d["other"])
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MECHANIC')""", (uuid4(),org,loc,subject))
    client = TestClient(app, base_url="https://127.0.0.1")
    base = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}"
    paths = [base + suffix for suffix in ("/customer", "/workspace", "/inspection-qc")]

    def preview_and_rule(allowed, cutoff=None):
        owner = token(client,d,d["other"])
        response = set_rule(client,url(org),owner,role="MECHANIC",loc=loc,
                            document="INTAKE_ACT",cap="READ",allowed=allowed,not_before=cutoff)
        assert response.status_code == 201,response.text
        preview = client.get(url(org)+"/effective",params={
            "locationId":str(loc),"userId":str(subject),"documentKind":"INTAKE_ACT",
        })
        assert preview.status_code == 200,preview.text
        assert preview.json()["capabilities"]["READ"]["allowed"] is allowed
        assert (preview.json()["capabilities"]["READ"]["notBefore"] is not None) == (cutoff is not None)

    def assert_paths(status, targets=paths):
        token(client,d,subject)
        for path in targets:
            response = client.get(path)
            assert response.status_code == status,(path,response.text)
            assert response.headers["cache-control"] == "no-store"
            if status == 403:
                for field in ("customerId","customerLinkId","steps","inspection"):
                    assert field not in response.json()

    assert_paths(200)
    preview_and_rule(False)
    assert_paths(403)
    # Личная ALLOW другой роли не отменяет запрет совмещённой роли.
    owner = token(client,d,d["other"])
    assert set_rule(client,url(org),owner,role="INTAKE",loc=loc,user=subject,
                    document="INTAKE_ACT",cap="READ",allowed=True).status_code == 201
    assert_paths(403)
    preview_and_rule(True,"2099-01-01T00:00:00Z")
    assert_paths(403)
    preview_and_rule(True)
    assert_paths(200)
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s
            AND role_code='INTAKE' AND revoked_at IS NULL""",(org,loc,subject))
    # MECHANIC alone may inspect, but cannot gain customer/workspace access.
    assert_paths(403,paths[:2])
    assert_paths(200,paths[2:])
