"""Real PG17 HTTP maker-checker inventory, fail-closed boundaries."""
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import token_digest
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup, allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant


def post(client,d,user,url,payload,key=None):
    client.cookies.set("rp_session",d["tokens"][user])
    return client.post(url,json=payload,headers={
        "Origin":"https://127.0.0.1",
        "X-CSRF-Token":d["csrf"][user],
        "Idempotency-Key":str(key or uuid4()),
    })


def test_http_count_and_independent_approval_replay_and_revoke(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,4)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
    base=f"/api/v1/dev/auto/organizations/{org}/stocktake"
    count_url=base+"/counts"
    approval_url=base+"/approvals"
    count_data={"locationId":str(loc),"lotId":str(lot),"actual":2,"reason":"manual recount"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        key=uuid4()
        counted=post(client,d,d["intake"],count_url,count_data,key)
        assert counted.status_code==201,counted.text
        assert counted.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],count_url,count_data,key).json()==counted.json()
        bad=post(client,d,d["intake"],count_url,{**count_data,"actual":3},key)
        assert bad.status_code==409
        count_id=counted.json()["countId"]
        approval_data={"locationId":str(loc),"countId":count_id}
        assert post(client,d,d["intake"],approval_url,approval_data).status_code==403
        assert post(client,d,d["manager"],count_url,count_data).status_code==403
        assert post(client,d,d["manager"],approval_url,
                    {**approval_data,"locationId":str(uuid4())}).status_code==403
        approval_key=uuid4()
        approved=post(client,d,d["manager"],approval_url,approval_data,approval_key)
        assert approved.status_code==201,approved.text
        assert post(client,d,d["manager"],approval_url,approval_data,approval_key).json()==approved.json()
        assert post(client,d,d["manager"],approval_url,approval_data).status_code==409
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==2 and balance(db,item)==(6,0)
            assert db.execute(
              "SELECT counted_by FROM auto_stock_lot_count WHERE id=%s",
              (count_id,)).fetchone()[0]==d["intake"]
            assert db.execute(
              "SELECT approved_by FROM auto_stock_lot_count_approval WHERE id=%s",
              (approved.json()["approvalId"],)).fetchone()[0]==d["manager"]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager_grant,))
        assert post(client,d,d["manager"],approval_url,approval_data,approval_key).status_code==403


def test_http_disabled_missing_csrf_and_input_fail_closed(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    url=f"/api/v1/dev/auto/organizations/{uuid4()}/stocktake/counts"
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(url,json={}).status_code==404
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,2)
    url=f"/api/v1/dev/auto/organizations/{org}/stocktake/counts"
    payload={"locationId":str(loc),"lotId":str(lot),"actual":1,"reason":"counted"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert client.post(url,json=payload,headers={"Origin":"https://127.0.0.1",
            "Idempotency-Key":str(uuid4())}).status_code==403
        assert post(client,d,d["intake"],url,{**payload,"actual":True}).status_code==422
        assert post(client,d,d["intake"],url,{**payload,"extraneous":5}).status_code==422
        assert client.post(url,json=payload,headers={
            "Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["intake"]]
        }).status_code==422
        assert post(client,d,d["intake"],url,
                    {**payload,"locationId":str(uuid4())}).status_code==403
        assert post(client,d,d["intake"],url,payload).status_code==201
