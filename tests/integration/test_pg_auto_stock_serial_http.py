"""Synthetic serial registration HTTP contract and role boundaries."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,act


def test_serial_http_replay_scope_and_csrf(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,grant_id=setup(db,d,2)
        act(db,d,org,loc,lot,"ALLOCATE",2)
    url=f"/api/v1/dev/auto/organizations/{org}/warehouse/serials"
    payload={"locationId":str(loc),"lotId":str(lot),"serialNumber":"SERIAL-001"}
    key=uuid4()
    with TestClient(app,base_url="https://127.0.0.1") as client:
        def post(user,body=payload,request_id=key,csrf=True):
            client.cookies.set("rp_session",d["tokens"][user])
            headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(request_id)}
            if csrf: headers["X-CSRF-Token"]=d["csrf"][user]
            return client.post(url,json=body,headers=headers)
        assert post(d["intake"],csrf=False).status_code==403
        assert post(d["manager"]).status_code==403
        assert post(d["intake"],{**payload,"locationId":str(uuid4())}).status_code==403
        assert post(d["intake"],{**payload,"serialNumber":""}).status_code==422
        created=post(d["intake"])
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        assert post(d["intake"]).json()==created.json()
        assert post(d["intake"],request_id=uuid4()).status_code==409
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        assert post(d["intake"]).status_code==403


def test_serial_http_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/serials",
                           json={}).status_code==404
