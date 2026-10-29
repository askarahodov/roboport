"""Protected synthetic mechanic acknowledgement HTTP contract."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup
from test_pg_auto_stock_service import grant


def test_handoff_http_role_csrf_replay(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,2)
        mech_grant=grant(db,d["manager"],org,loc,"MECHANIC")
        issue=issue_work_order_from_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),1)
    url=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders/acknowledge"
    payload={"locationId":str(loc),"issueId":str(issue)}
    key=uuid4()
    with TestClient(app,base_url="https://127.0.0.1") as client:
        def submit(actor,body=payload,ident=key,csrf=True):
            client.cookies.set("rp_session",d["tokens"][actor])
            headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(ident)}
            if csrf: headers["X-CSRF-Token"]=d["csrf"][actor]
            return client.post(url,json=body,headers=headers)
        assert submit(d["manager"],csrf=False).status_code==403
        assert submit(d["intake"]).status_code==403
        assert submit(d["manager"],{**payload,"locationId":str(uuid4())}).status_code==403
        assert submit(d["manager"],{**payload,"issueId":"wrong"}).status_code==422
        success=submit(d["manager"])
        assert success.status_code==201,success.text
        assert success.headers["cache-control"]=="no-store"
        assert submit(d["manager"]).json()==success.json()
        assert submit(d["manager"],ident=uuid4()).status_code==409
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (mech_grant,))
        assert submit(d["manager"]).status_code==403


def test_handoff_http_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(
            f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/work-orders/acknowledge",
            json={}).status_code==404
