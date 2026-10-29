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
