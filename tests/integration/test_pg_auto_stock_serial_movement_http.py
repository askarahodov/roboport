"""Synthetic HTTP serial movement, original-branch replay and revocation."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_stock_serial import register_lot_serial
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup as work_order_setup
from test_pg_auto_stock_interbranch_transfer import setup as transfer_setup


def post(client,d,actor,url,payload,key=None,csrf=True):
    client.cookies.set("rp_session",d["tokens"][actor])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf: headers["X-CSRF-Token"]=d["csrf"][actor]
    return client.post(url,json=payload,headers=headers)


def test_http_serial_issue_return_scope_csrf_replay_revoke(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,grant=work_order_setup(db,d,1)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,loc,lot,uuid4(),"HTTP-TRACKED-001")
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/serial-movements"
    body={"locationId":str(loc),"serialId":str(serial),"workOrderId":str(order)}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        ordinary_issue=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders/issue"
        untracked_issue={"locationId":str(loc),"workOrderId":str(order),
                         "lotId":str(lot),"quantity":1}
        assert post(client,d,d["intake"],ordinary_issue,untracked_issue).status_code==409
        assert post(client,d,d["intake"],base+"/issue",body,csrf=False).status_code==403
        assert post(client,d,d["manager"],base+"/issue",body).status_code==403
        assert post(client,d,d["intake"],base+"/issue",
                    {**body,"locationId":str(uuid4())}).status_code==403
        assert post(client,d,d["intake"],base+"/issue",
                    {**body,"destinationLotId":str(lot)}).status_code==422
        assert post(client,d,d["intake"],base+"/issue",
                    {"locationId":str(loc),"serialId":str(serial)}).status_code==422
        key=uuid4()
        created=post(client,d,d["intake"],base+"/issue",body,key)
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],base+"/issue",body,key).json()==created.json()
        assert post(client,d,d["intake"],base+"/issue",body).status_code==409
        # Plain quantity return cannot silently clear the physical issue while
        # a serialized item remains ISSUED: stable HTTP 409, never a 500.
        ordinary=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders/return"
        untracked={"locationId":str(loc),"workOrderId":str(order),
                   "lotId":str(lot),"quantity":1}
        assert post(client,d,d["intake"],ordinary,untracked).status_code==409
        result=post(client,d,d["intake"],base+"/return",body)
        assert result.status_code==201,result.text
        assert post(client,d,d["intake"],base+"/return",body).status_code==409
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant,))
        assert post(client,d,d["intake"],base+"/issue",body,key).status_code==403


def test_http_serial_transfer_dual_branch_replay_and_revoke(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,a,b,sa,sb,la,lb,ga,gb=transfer_setup(db,d,2)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,a,la,uuid4(),"HTTP-SERIAL-XFER")
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/serial-movements/transfer"
    body={"locationId":str(a),"serialId":str(serial),"destinationLotId":str(lb)}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        key=uuid4()
        created=post(client,d,d["intake"],base,body,key)
        assert created.status_code==201,created.text
        assert post(client,d,d["intake"],base,body,key).json()==created.json()
        with psycopg.connect(DSN) as db:
            assert db.execute("SELECT lot_id FROM auto_stock_serial_current WHERE serial_id=%s",
                              (serial,)).fetchone()[0]==lb
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (ga,))
        assert post(client,d,d["intake"],base,body,key).status_code==403


def test_http_serial_movement_off(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/serial-movements/issue",
                           json={}).status_code==404
