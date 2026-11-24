"""PG17: remaining physical stock operations and report enforce live ACL."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_service import AutoStockDenied,mutate_auto_stock
from roboport_api.auto_stock_disposition import dispose_stock_lot
from roboport_api.auto_stock_lot_allocation import mutate_stock_lot_allocation
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_reclassification import seeded
from test_pg_auto_stock_reclassification_http import post
from test_pg_auto_stock_lot_allocation import setup,allocated,act
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance
from test_pg_auto_document_access_http import admin
from test_pg_auto_stock_order_serial_policy_http import policy
from test_pg_auto_work_order_estimate_http import api,token
from test_pg_auto_stock_abc_xyz_http import last_months,url


def test_writeoff_and_direct_stock_policy(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,_=setup(db,d,4)
        act(db,d,org,loc,lot,"ALLOCATE",4)
        grant(db,d["manager"],org,loc,"MANAGER")
        admin(db,org,d["other"])
    warehouse,manager=d["intake"],d["manager"]
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/writeoffs"
    params={"locationId":str(loc)}
    body={**params,"lotId":str(lot),"quantity":2,"reasonCode":"DAMAGED","note":"Синтетический акт"}
    policy(http,d,org,loc,"WAREHOUSE","CREATE")
    token(http,d,warehouse)
    report=http.get(base,params=params)
    assert report.status_code==200,report.text
    assert report.json()["canDraft"] is False and report.json()["lots"]==[]
    key=uuid4()
    assert post(http,d,warehouse,base,body,key).status_code==403
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockDenied):
            mutate_auto_stock(db,d["tokens"][warehouse],org,loc,sku,uuid4(),"RECEIPT",1)
        assert balance(db,sku)==(4,0)
    policy(http,d,org,loc,"WAREHOUSE","CREATE",True)
    created=post(http,d,warehouse,base,body,key)
    assert created.status_code==201,created.text
    decision=base+"/"+created.json()["documentId"]+"/decisions"
    review={**params,"action":"APPROVE","note":"Независимое решение"}
    policy(http,d,org,loc,"MANAGER","CLOSE")
    token(http,d,manager)
    state=http.get(base,params=params)
    assert state.status_code==200,state.text
    assert state.json()["items"][0]["canReview"] is False
    approve_key=uuid4()
    assert post(http,d,manager,decision,review,approve_key).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","EDIT")
    policy(http,d,org,loc,"WAREHOUSE","CLOSE")
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockDenied):
            mutate_stock_lot_allocation(db,d["tokens"][warehouse],org,loc,lot,uuid4(),"DEALLOCATE",1)
        with pytest.raises(AutoStockDenied):
            dispose_stock_lot(db,d["tokens"][warehouse],org,loc,lot,uuid4(),"WRITE_OFF",1,"Test blocked")
        assert allocated(db,lot)==4 and balance(db,sku)==(4,0)
    policy(http,d,org,loc,"MANAGER","CLOSE",True)
    approved=post(http,d,manager,decision,review,approve_key)
    assert approved.status_code==201,approved.text
    policy(http,d,org,loc,"MANAGER","READ",True,"2099-01-01T00:00:00Z")
    token(http,d,manager)
    assert http.get(base,params=params).status_code==403
    assert post(http,d,manager,decision,review,approve_key).status_code==403
    months=last_months()
    report=url(org,loc)
    query={"fromMonth":months[0],"toMonth":months[-1]}
    assert http.get(report,params=query).status_code==403
    assert http.get(report+'/export',params=query).status_code==403
    policy(http,d,org,loc,"MANAGER","READ",True)
    token(http,d,manager)
    assert http.get(report,params=query).status_code==200
    assert http.get(report+'/export',params=query).status_code==200
    replay=post(http,d,manager,decision,review,approve_key)
    assert replay.status_code==200 and replay.json()["decisionId"]==approved.json()["decisionId"]
    with psycopg.connect(DSN) as db:
        assert allocated(db,lot)==2 and balance(db,sku)==(2,0)


def test_reclassification_maker_checker_document_policy(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,_,_=seeded(db,d,3)
        admin(db,org,d["other"])
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/reclassifications"
    body={"locationId":str(loc),"sourceLotId":str(src),"destinationLotId":str(dst),
          "quantity":2,"reason":"Контрольная пересортица"}
    policy(http,d,org,loc,"WAREHOUSE","CREATE")
    key=uuid4()
    assert post(http,d,d["intake"],base,body,key).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","CREATE",True)
    created=post(http,d,d["intake"],base,body,key)
    assert created.status_code==201,created.text
    approval={"locationId":str(loc),"documentId":created.json()["documentId"]}
    approve_key=uuid4()
    policy(http,d,org,loc,"MANAGER","CLOSE")
    assert post(http,d,d["manager"],base+"/approvals",approval,approve_key).status_code==403
    with psycopg.connect(DSN) as db:
        assert allocated(db,src)==3 and allocated(db,dst)==0
    policy(http,d,org,loc,"MANAGER","CLOSE",True)
    approved=post(http,d,d["manager"],base+"/approvals",approval,approve_key)
    assert approved.status_code==201,approved.text
    policy(http,d,org,loc,"MANAGER","WRITE")
    assert post(http,d,d["manager"],base+"/approvals",approval,approve_key).status_code==403
    with psycopg.connect(DSN) as db:
        assert allocated(db,src)==1 and allocated(db,dst)==2
        assert balance(db,ss)==(1,0) and balance(db,ds)==(2,0)
