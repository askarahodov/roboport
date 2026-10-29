"""Synthetic PG17 HTTP reclassification maker/checker, replay and isolation."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_reclassification import seeded
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance


def post(client,d,actor,url,body,key=None,csrf=True):
    client.cookies.set("rp_session",d["tokens"][actor])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf:headers["X-CSRF-Token"]=d["csrf"][actor]
    return client.post(url,headers=headers,json=body)


def test_http_reclassification_dual_control_replay_tenant_and_csrf(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,warehouse,manager=seeded(db,d,3)
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/reclassifications"
    body={"locationId":str(loc),"sourceLotId":str(src),
          "destinationLotId":str(dst),"quantity":2,
          "reason":"Ошибка классификации артикула"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert post(client,d,d["intake"],base,body,csrf=False).status_code==403
        assert post(client,d,d["manager"],base,body).status_code==403
        assert post(client,d,d["intake"],base,{**body,"quantity":True}).status_code==422
        assert post(client,d,d["intake"],base,
                    {**body,"locationId":str(uuid4())}).status_code==403
        key=uuid4()
        created=post(client,d,d["intake"],base,body,key)
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],base,body,key).json()==created.json()
        assert post(client,d,d["intake"],base,
            {**body,"quantity":1},key).status_code==409
        doc=created.json()["documentId"]
        assert post(client,d,d["intake"],base+"/approvals",
            {"locationId":str(loc),"documentId":doc}).status_code==403
        approval_body={"locationId":str(loc),"documentId":doc}
        assert post(client,d,d["manager"],base+"/approvals",
            {**approval_body,"locationId":str(uuid4())}).status_code==403
        approval_key=uuid4()
        approved=post(client,d,d["manager"],base+"/approvals",approval_body,approval_key)
        assert approved.status_code==201,approved.text
        assert post(client,d,d["manager"],base+"/approvals",
            approval_body,approval_key).json()==approved.json()
        with psycopg.connect(DSN) as db:
            assert balance(db,ss)==(1,0) and balance(db,ds)==(2,0)
            assert allocated(db,src)==1 and allocated(db,dst)==2
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager,))
        assert post(client,d,d["manager"],base+"/approvals",
                    approval_body,approval_key).status_code==403


def test_http_reclassification_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/reclassifications",
                           json={}).status_code==404
