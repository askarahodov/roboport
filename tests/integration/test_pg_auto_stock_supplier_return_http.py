"""End-to-end HTTP security and accounting of independently approved supplier returns."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt,value
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance


def post(client,d,who,url,data,key=None,csrf=True):
    client.cookies.set("rp_session",d["tokens"][who])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf:headers["X-CSRF-Token"]=d["csrf"][who]
    return client.post(url,headers=headers,json=data)


def test_supplier_return_http_dual_control_replay_scope_vat_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        value(db,d,org,loc,receipt,vat=301)
    endpoint=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    payload={"locationId":str(loc),"sourceReceiptId":str(receipt),"quantity":2,
       "reason":"Возврат несоответствующей детали","supplierReference":"RMA-2026-001"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert post(client,d,d["intake"],endpoint,payload,csrf=False).status_code==403
        assert post(client,d,d["manager"],endpoint,payload).status_code==403
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"quantity":True}).status_code==422
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"locationId":str(uuid4())}).status_code==403
        key=uuid4()
        created=post(client,d,d["intake"],endpoint,payload,key)
        assert created.status_code==201,created.text
        assert created.headers["cache-control"]=="no-store"
        assert post(client,d,d["intake"],endpoint,payload,key).json()==created.json()
        assert post(client,d,d["intake"],endpoint,
                    {**payload,"quantity":1},key).status_code==409
        document=created.json()["documentId"]
        assert post(client,d,d["intake"],endpoint+"/approvals",
                    {"locationId":str(loc),"documentId":document}).status_code==403
        approval_payload={"locationId":str(loc),"documentId":document}
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    {**approval_payload,"locationId":str(uuid4())}).status_code==403
        approval_key=uuid4()
        approved=post(client,d,d["manager"],endpoint+"/approvals",approval_payload,approval_key)
        assert approved.status_code==201,approved.text
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    approval_payload,approval_key).json()==approved.json()
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==0 and balance(db,sku)==(1,0)
            assert db.execute("""SELECT purchase_net_attributed_minor,
                purchase_vat_attributed_minor FROM auto_stock_supplier_return_acceptance
                WHERE id=%s""",(approved.json()["approvalId"],)).fetchone()==(2500,301)
            manager=db.execute("""SELECT id FROM auto_organization_role_grant
                WHERE user_id=%s AND organization_id=%s AND location_id=%s
                  AND role_code='MANAGER' AND revoked_at IS NULL
                ORDER BY created_at DESC LIMIT 1""",
                (d["manager"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager,))
        assert post(client,d,d["manager"],endpoint+"/approvals",
                    approval_payload,approval_key).status_code==403


def test_supplier_return_http_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(
          f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/supplier-returns",
          json={}).status_code==404



def test_supplier_return_manager_queue_full_draft_to_approval_lifecycle(monkeypatch):
    """Scoped review, keyset page, own-draft exclusion, revoke and finance evidence."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,3)
        value(db,d,org,loc,receipt,vat=301)
    endpoint=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns"
    review=endpoint+"/pending"
    params={"locationId":str(loc)}
    body={"locationId":str(loc),"sourceReceiptId":str(receipt),
          "quantity":1,"reason":"Возврат после осмотра",
          "supplierReference":"RMA-PENDING-001"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.get(review,params=params).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert client.get(review,params=params).status_code==403
        a=post(client,d,d["intake"],endpoint,body)
        b=post(client,d,d["intake"],endpoint,{
            **body,"supplierReference":"RMA-PENDING-002"})
        assert a.status_code==201 and b.status_code==201
        ids={a.json()["documentId"],b.json()["documentId"]}
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        first=client.get(review,params={**params,"limit":1})
        assert first.status_code==200,first.text
        assert first.headers["cache-control"]=="no-store"
        assert first.json()["syntheticOnly"] is True
        assert len(first.json()["items"])==1
        cursor=first.json()["nextDocumentId"]
        assert cursor==first.json()["items"][0]["documentId"]
        second=client.get(review,params={**params,"limit":1,
            "afterDocumentId":cursor})
        assert second.status_code==200,second.text
        assert second.json()["nextDocumentId"] is None
        assert {first.json()["items"][0]["documentId"],
                second.json()["items"][0]["documentId"]}==ids
        for item in first.json()["items"]+second.json()["items"]:
            assert item["sourceReceiptId"]==str(receipt)
            assert item["lotId"]==str(lot)
            assert item["quantity"]==1
            assert item["reason"]=="Возврат после осмотра"
            assert item["supplierReference"].startswith("RMA-PENDING")
            for forbidden in ("createdBy","acceptedBy","requestId","customerId","vehicleId"):
                assert forbidden not in item
        assert str(d["vehicle"]) not in first.text
        assert str(d["intake"]) not in first.text
        assert client.get(review,params={**params,"limit":0}).status_code==422
        assert client.get(review,params={**params,"limit":51}).status_code==422
        assert client.get(review,params={**params,"afterDocumentId":"bad"}).status_code==422
        assert client.get(review,params={"locationId":str(uuid4())}).status_code==403

        chosen=first.json()["items"][0]["documentId"]
        approved=post(client,d,d["manager"],endpoint+"/approvals",
                      {"locationId":str(loc),"documentId":chosen})
        assert approved.status_code==201,approved.text
        pending=client.get(review,params=params)
        assert pending.status_code==200,pending.text
        assert [row["documentId"] for row in pending.json()["items"]]==[
            next(i for i in ids if i!=chosen)]
        with psycopg.connect(DSN) as db:
            assert allocated(db,lot)==2
            assert balance(db,sku)==(3,0)
            grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
                WHERE user_id=%s AND organization_id=%s AND location_id=%s
                  AND role_code='MANAGER' AND revoked_at IS NULL
                  ORDER BY created_at DESC LIMIT 1""",
                (d["manager"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant_id,))
        assert client.get(review,params=params).status_code==403
