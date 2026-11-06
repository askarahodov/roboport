"""PG17 HTTP warehouse receipt maker-checker and fail-closed ACL."""
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant


def send(client,d,actor,path,payload,key=None,csrf=True):
    client.cookies.set('rp_session',d['tokens'][actor])
    headers={'Origin':'https://127.0.0.1','Idempotency-Key':str(key or uuid4())}
    if csrf:
        headers['X-CSRF-Token']=d['csrf'][actor]
    return client.post(path,json=payload,headers=headers)


def test_receipt_http_two_users_accounted_once(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,role=setup(db,d,2)
        grant(db,d['manager'],org,loc,'MANAGER')
    base=f'/api/v1/dev/auto/organizations/{org}/warehouse/receipts'
    body={'locationId':str(loc),'lotId':str(lot),'quantity':3,
          'sourceReference':'SUPPLIER-RECEIPT-42'}
    with TestClient(app,base_url='https://127.0.0.1') as client:
        assert send(client,d,d['intake'],base,body,csrf=False).status_code==403
        assert send(client,d,d['intake'],base,{**body,'quantity':True}).status_code==422
        assert send(client,d,d['intake'],base,{**body,'locationId':str(uuid4())}).status_code==403
        draft_key=uuid4()
        response=send(client,d,d['intake'],base,body,draft_key)
        assert response.status_code==201,response.text
        assert response.headers['cache-control']=='no-store'
        doc_id=response.json()['documentId']
        assert send(client,d,d['intake'],base,body,draft_key).json()==response.json()
        assert send(client,d,d['intake'],base,{**body,'quantity':4},draft_key).status_code==409
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(2,0) and allocated(db,lot)==0
        acceptance={'locationId':str(loc),'documentId':doc_id}
        assert send(client,d,d['intake'],base+'/accept',acceptance).status_code==403
        assert send(client,d,d['manager'],base,body).status_code==403
        approval_key=uuid4()
        applied=send(client,d,d['manager'],base+'/accept',acceptance,approval_key)
        assert applied.status_code==201,applied.text
        assert send(client,d,d['manager'],base+'/accept',acceptance,approval_key).json()==applied.json()
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(5,0) and allocated(db,lot)==3
            assert db.execute("""SELECT d.created_by,a.accepted_by
             FROM auto_stock_receipt_acceptance a
             JOIN auto_stock_receipt_document d ON d.id=a.document_id
             WHERE a.id=%s""",(applied.json()['acceptanceId'],)).fetchone()==(
               d['intake'],d['manager'])
            db.execute('UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s',(role,))
        assert send(client,d,d['intake'],base,body,draft_key).status_code==403


def test_receipt_http_disabled_is_404(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url='https://127.0.0.1') as client:
        base=f'/api/v1/dev/auto/organizations/{uuid4()}/warehouse/receipts'
        assert client.post(base,json={}).status_code==404
        assert client.post(base+'/accept',json={}).status_code==404



def test_receipt_manager_queues_cover_draft_acceptance_valuation_and_revoke(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,2)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts"
    queue=base+"/pending"
    params={"locationId":str(loc),"stage":"accept"}
    body={"locationId":str(loc),"lotId":str(lot),"quantity":3,
          "sourceReference":"SUPPLIER-PENDING-001"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.get(queue,params=params).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert client.get(queue,params=params).status_code==403
        a=send(client,d,d["intake"],base,body)
        b=send(client,d,d["intake"],base,
               {**body,"sourceReference":"SUPPLIER-PENDING-002"})
        assert a.status_code==201 and b.status_code==201
        ids={a.json()["documentId"],b.json()["documentId"]}
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        first=client.get(queue,params={**params,"limit":1})
        assert first.status_code==200,first.text
        assert first.headers["cache-control"]=="no-store"
        assert first.json()["syntheticOnly"] is True
        assert first.json()["stage"]=="accept"
        assert len(first.json()["items"])==1
        cursor=first.json()["nextDocumentId"]
        assert cursor==first.json()["items"][0]["documentId"]
        second=client.get(queue,params={**params,"limit":1,"afterDocumentId":cursor})
        assert second.status_code==200,second.text
        assert second.json()["nextDocumentId"] is None
        assert {first.json()["items"][0]["documentId"],
                second.json()["items"][0]["documentId"]}==ids
        for row in first.json()["items"]+second.json()["items"]:
            assert row["quantity"]==3
            assert row["lotId"]==str(lot)
            assert row["sourceReference"].startswith("SUPPLIER-PENDING-")
            assert "createdBy" not in row and "requestId" not in row
        assert str(d["intake"]) not in first.text
        assert str(d["vehicle"]) not in first.text
        assert client.get(queue,params={**params,"limit":0}).status_code==422
        assert client.get(queue,params={**params,"limit":51}).status_code==422
        assert client.get(queue,params={**params,"stage":"invalid"}).status_code==422
        assert client.get(queue,params={**params,"afterDocumentId":"not-uuid"}).status_code==422
        assert client.get(queue,params={**params,"locationId":str(uuid4())}).status_code==403

        document_id=first.json()["items"][0]["documentId"]
        accept=send(client,d,d["manager"],base+"/accept",
                    {"locationId":str(loc),"documentId":document_id})
        assert accept.status_code==201,accept.text
        after=client.get(queue,params=params)
        assert after.status_code==200
        assert len(after.json()["items"])==1
        assert after.json()["items"][0]["documentId"]!=document_id
        values=client.get(queue,params={**params,"stage":"value"})
        assert values.status_code==200,values.text
        assert [v["documentId"] for v in values.json()["items"]]==[document_id]

        from datetime import datetime,timezone,timedelta
        valuation=send(client,d,d["manager"],base+"/valuation",{
            "locationId":str(loc),"documentId":document_id,
            "currency":"RUB","unitNetMinor":1250,"vatTotalMinor":300,
            "supplierPriceRevision":"REV-QUEUE-001",
            "supplierQuotedAt":(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        })
        assert valuation.status_code==201,valuation.text
        after_value=client.get(queue,params={**params,"stage":"value"})
        assert after_value.status_code==200
        assert after_value.json()["items"]==[]
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(5,0) and allocated(db,lot)==3
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (manager_grant,))
        assert client.get(queue,params=params).status_code==403
        assert client.get(queue,params={**params,"stage":"value"}).status_code==403
