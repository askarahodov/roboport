"""PG17 HTTP lot issue/return: order reservation, live role, no duplicate dispatch."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_lot_allocation import allocated


def post(client,token,csrf,url,payload,key=None):
    client.cookies.set("rp_session",token)
    return client.post(url,json=payload,headers={
        "Origin":"https://127.0.0.1","X-CSRF-Token":csrf,
        "Idempotency-Key":str(key or uuid4())})


def test_http_workorder_issue_return_and_replay(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,grant_id=setup(db,d,3)
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders"
    data={"locationId":str(loc),"workOrderId":str(order),"lotId":str(lot),"quantity":2}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        key=uuid4()
        issued=post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    base+"/issue",data,key)
        assert issued.status_code==201,issued.text
        assert issued.headers["cache-control"]=="no-store"
        assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    base+"/issue",data,key).json()==issued.json()
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(1,1) and allocated(db,lot)==1
        assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    base+"/issue",{**data,"quantity":3},key).status_code==409
        returned=post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                      base+"/return",data)
        assert returned.status_code==201,returned.text
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(3,1) and allocated(db,lot)==3
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant_id,))
        assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    base+"/issue",data,key).status_code==403
        assert post(client,d["tokens"][d["manager"]],d["csrf"][d["manager"]],
                    base+"/return",data).status_code==403


def test_http_workorder_scope_csrf_and_strict_body(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,2)
    url=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders/issue"
    data={"locationId":str(loc),"workOrderId":str(order),"lotId":str(lot),"quantity":1}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert client.post(url,json=data,headers={"Idempotency-Key":str(uuid4()),
             "Origin":"https://127.0.0.1"}).status_code==403
        for bad in ({**data,"quantity":True},{**data,"quantity":0},
                    {**data,"unexpected":"x"}):
            assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                        url,bad).status_code==422
        assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    url,{**data,"locationId":str(uuid4())}).status_code==403
        assert post(client,d["tokens"][d["intake"]],d["csrf"][d["intake"]],
                    url,{**data,"workOrderId":str(uuid4())}).status_code==403


def test_http_workorder_endpoints_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        for suffix in ("issue","return"):
            assert client.post(
                f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/work-orders/{suffix}",
                json={}).status_code==404
