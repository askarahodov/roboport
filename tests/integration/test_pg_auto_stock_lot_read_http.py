"""PG17 warehouse lot discovery: live ACL, branch isolation, keyset."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup, act
from test_pg_auto_stock_bin_lot import add_bin, add_lot
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import sku


def test_list_warehouse_lots_by_branch_and_revoke(monkeypatch):
    monkeypatch.setenv('ROBOport_SYNTHETIC_AUTO_API','1')
    monkeypatch.setenv('ROBOport_SYNTHETIC_AUTH_API','1')
    monkeypatch.setenv('ROBOport_DATABASE_URL',DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,grant_id=setup(db,d,5)
        act(db,d,org,loc,lot,'ALLOCATE',3)
        other=add_lot(db,org,loc,item,add_bin(db,org,loc,'DISCOVER-2'),batch='OTHER-2')
        foreign_org=uuid4()
    url=f'/api/v1/dev/auto/organizations/{org}/stocktake/lots'
    with TestClient(app,base_url='https://127.0.0.1') as client:
        client.cookies.set('rp_session',d['tokens'][d['intake']])
        response=client.get(url,params={'locationId':str(loc),'limit':1})
        assert response.status_code==200,response.text
        assert response.headers['cache-control']=='no-store'
        body=response.json()
        assert len(body['items'])==1 and body['nextLotId']
        first=body['items'][0]
        assert set(first)=={'lotId','article','batchCode','binCode','allocated','onHand','reserved'}
        second=client.get(url,params={'locationId':str(loc),'limit':1,
                                      'afterLotId':body['nextLotId']}).json()
        assert len(second['items'])==1 and second['nextLotId'] is None
        ids={first['lotId'],second['items'][0]['lotId']}
        assert ids=={str(lot),str(other)}
        listed=first if first['lotId']==str(lot) else second['items'][0]
        assert listed['allocated']==3 and listed['onHand']==5 and listed['reserved']==0
        assert client.get(url,params={'locationId':str(uuid4())}).status_code==403
        assert client.get(f'/api/v1/dev/auto/organizations/{foreign_org}/stocktake/lots',
                          params={'locationId':str(loc)}).status_code==403
        assert client.get(url,params={'locationId':str(loc),'limit':0}).status_code==422
        assert client.get(url,params={'locationId':str(loc),'afterLotId':'oops'}).status_code==422
        client.cookies.set('rp_session',d['tokens'][d['manager']])
        assert client.get(url,params={'locationId':str(loc)}).status_code==403
        with psycopg.connect(DSN) as db:
            manager_grant=grant(db,d['manager'],org,loc,'MANAGER')
        assert client.get(url,params={'locationId':str(loc)}).status_code==200
        with psycopg.connect(DSN) as db:
            db.execute('UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s',(manager_grant,))
        assert client.get(url,params={'locationId':str(loc)}).status_code==403
        client.cookies.set('rp_session',d['tokens'][d['intake']])
        with psycopg.connect(DSN) as db:
            db.execute('UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s',(grant_id,))
        assert client.get(url,params={'locationId':str(loc)}).status_code==403


def test_lots_endpoint_disabled(monkeypatch):
    monkeypatch.setenv('ROBOport_SYNTHETIC_AUTO_API','0')
    monkeypatch.setenv('ROBOport_DATABASE_URL',DSN)
    with TestClient(app,base_url='https://127.0.0.1') as client:
        assert client.get(f'/api/v1/dev/auto/organizations/{uuid4()}/stocktake/lots',
                          params={'locationId':str(uuid4())}).status_code==404
