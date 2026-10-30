"""HTTP financial read stays loopback-only, manager-only, and tenant-scoped."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_auto_order_customer_read import prepare
from test_pg_baseline import DSN


def test_financial_http_manager_scope_revoke_and_gate(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d, org, loc, order, foreign, foreign_loc = prepare()
    with psycopg.connect(DSN) as db:
        role = uuid4()
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""",(role,org,loc,d["manager"]))
        db.execute("SELECT record_auto_work_order_financial(%s,%s,'CHARGE',%s,%s)",
                   (order,uuid4(),4500,"synthetic service"))
    client = TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/financial-balance"
    assert client.get(path).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert client.get(path).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["manager"]])
    good=client.get(path)
    assert good.status_code==200,good.text
    assert good.headers["cache-control"]=="no-store"
    assert good.json()=={"orderId":str(order),"balanceMinor":4500,"currency":None,"syntheticOnly":True}
    assert client.get(path.replace(str(org),str(foreign)).replace(str(loc),str(foreign_loc))).status_code==403
    assert client.get(path.replace(str(order),str(uuid4()))).status_code==403
    assert client.get(path,headers={"X-Forwarded-Proto":"https"}).status_code==404
    assert TestClient(app,base_url="http://127.0.0.1").get(path).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
    assert client.get(path).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(path).status_code==404
