"""Financial event history is tenant- and role-scoped, with no stale proof."""
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_order_financial_read import list_work_order_financial_events
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_auto_order_customer_read import prepare
from test_pg_baseline import DSN


def test_financial_history_scoped_and_revoked(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, order, foreign, foreign_loc = prepare()
    with psycopg.connect(DSN) as db:
        role = uuid4()
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""", (role,org,loc,d["manager"]))
        for action, amount, reason in (("CHARGE",5000,"synthetic labor"),
                                       ("CREDIT",1000,"synthetic correction")):
            db.execute("SELECT record_auto_work_order_financial(%s,%s,%s,%s,%s)",
                       (order,uuid4(),action,amount,reason))
    client = TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/financial-history"
    assert client.get(url).status_code == 403
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    assert client.get(url).status_code == 403
    client.cookies.set("rp_session", d["tokens"][d["manager"]])
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data=response.json()
    assert data["balanceMinor"] == 4000 and data["syntheticOnly"] is True
    assert {x["action"] for x in data["items"]} == {"CHARGE","CREDIT"}
    assert len(data["items"]) == 2
    assert len(client.get(url+"?limit=1").json()["items"]) == 1
    assert client.get(url+"?limit=101").status_code == 422
    assert client.get(url.replace(str(org),str(foreign)).replace(str(loc),str(foreign_loc))).status_code == 403
    assert client.get(url.replace(str(order),str(uuid4()))).status_code == 403
    assert client.get(url,headers={"X-Forwarded-Proto":"https"}).status_code == 404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (role,))
    assert client.get(url).status_code == 403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(url).status_code == 404


def test_history_proof_revocation_and_input_fail_closed():
    d,org,loc,order,_,_ = prepare()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""",
          (uuid4(),org,loc,d["manager"]))
        token=d["tokens"][d["manager"]]
        assert list_work_order_financial_events(db,token,org,loc,order)["items"] == []
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
        with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
            list_work_order_financial_events(db,token,org,loc,order)
    with pytest.raises(AutoStockDenied,match="INVALID_INPUT"):
        list_work_order_financial_events(None,"session",uuid4(),uuid4(),uuid4(),limit=True)
