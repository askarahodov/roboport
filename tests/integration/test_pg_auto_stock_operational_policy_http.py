"""PG17: inventory, transfer and reconciliation obey live document policies."""
from uuid import uuid4
import psycopg

from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_lot_allocation import setup, allocated
from test_pg_auto_stock_interbranch_transfer import setup as transfer_setup
from test_pg_auto_stocktake_http import post
from test_pg_auto_document_access_http import admin, set_rule, url
from test_pg_auto_work_order_estimate_http import api, token
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_bin_lot import add_bin, add_lot


def policy(http,d,org,loc,role,cap,allowed=False,cutoff=None,user=None):
    owner=token(http,d,d["other"])
    response=set_rule(http,url(org),owner,loc=loc,role=role,cap=cap,
        document="SUPPLIER_ORDER",allowed=allowed,not_before=cutoff,user=user)
    assert response.status_code==201,response.text


def test_stocktake_and_reconciliation_denial_and_restore(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,_=setup(db,d,4)
        grant(db,d["manager"],org,loc,"MANAGER")
        admin(db,org,d["other"])
    stock=f"/api/v1/dev/auto/organizations/{org}/stocktake"
    report=f"/api/v1/dev/auto/organizations/{org}/warehouse/reconciliation"
    params={"locationId":str(loc)}
    payload={**params,"lotId":str(lot),"actual":2,"reason":"Контрольный пересчёт"}
    key=uuid4()
    token(http,d,d["intake"])
    assert http.get(stock+"/lots",params=params).status_code==200
    assert http.get(report,params=params).status_code==200
    policy(http,d,org,loc,"WAREHOUSE","CREATE")
    assert post(http,d,d["intake"],stock+"/counts",payload,key).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","CREATE",True)
    created=post(http,d,d["intake"],stock+"/counts",payload,key)
    assert created.status_code==201,created.text
    approval={**params,"countId":created.json()["countId"]}
    policy(http,d,org,loc,"WAREHOUSE","READ")
    policy(http,d,org,loc,"WAREHOUSE","READ",True,user=d["intake"])
    token(http,d,d["intake"])
    assert http.get(stock+"/lots",params=params).status_code==403
    assert http.get(report,params=params).status_code==403
    assert http.get(report+"/"+str(sku),params={**params,"kind":"events"}).status_code==403
    assert post(http,d,d["intake"],stock+"/counts",payload,key).status_code==403
    policy(http,d,org,loc,"MANAGER","READ",True,"2099-01-01T00:00:00Z")
    token(http,d,d["manager"])
    assert http.get(stock+"/pending",params=params).status_code==403
    assert post(http,d,d["manager"],stock+"/approvals",approval).status_code==403
    policy(http,d,org,loc,"MANAGER","READ",True)
    policy(http,d,org,loc,"MANAGER","CLOSE")
    token(http,d,d["manager"])
    assert http.get(stock+"/pending",params=params).status_code==200
    assert post(http,d,d["manager"],stock+"/approvals",approval).status_code==403
    policy(http,d,org,loc,"MANAGER","CLOSE",True)
    approved=post(http,d,d["manager"],stock+"/approvals",approval)
    assert approved.status_code==201,approved.text
    policy(http,d,org,loc,"MANAGER","PRICE_PURCHASE")
    token(http,d,d["manager"])
    physical=http.get(report,params=params)
    assert physical.status_code==200,physical.text
    assert physical.json()["canViewPurchases"] is False
    assert http.get(report+"/"+str(sku),params={**params,"kind":"receipts"}).status_code==403
    with psycopg.connect(DSN) as db:
        assert allocated(db,lot)==2 and balance(db,sku)==(6,0)
        assert db.execute("SELECT count(*) FROM auto_stock_lot_count WHERE lot_id=%s",
                          (lot,)).fetchone()==(1,)


def test_transfer_policy_both_branches_and_local_replay(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,src,dst,sa,sb,la,lb,_,_=transfer_setup(db,d,5)
        local=add_lot(db,org,src,sa,add_bin(db,org,src,"POLICY-LOCAL"),"POLICY-LOT")
        admin(db,org,d["other"])
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/interbranch-transfers"
    params={"sourceLocationId":str(src),"destinationLocationId":str(dst)}
    body={**params,"sourceLotId":str(la),"destinationLotId":str(lb),"quantity":2}
    token(http,d,d["intake"])
    assert http.get(base,params=params).status_code==200
    key=uuid4()
    policy(http,d,org,dst,"WAREHOUSE","EDIT")
    assert post(http,d,d["intake"],base,body,key).status_code==403
    policy(http,d,org,dst,"WAREHOUSE","EDIT",True)
    created=post(http,d,d["intake"],base,body,key)
    assert created.status_code==201,created.text
    policy(http,d,org,dst,"WAREHOUSE","READ")
    token(http,d,d["intake"])
    assert http.get(base,params=params).status_code==403
    assert post(http,d,d["intake"],base,body,key).status_code==403
    policy(http,d,org,dst,"WAREHOUSE","READ",True)
    assert post(http,d,d["intake"],base,body,key).status_code==200
    from roboport_api.auto_stock_interbranch_transfer import confirm_local_transfer
    from roboport_api.auto_stock_service import AutoStockDenied
    import pytest
    local_key=uuid4()
    with psycopg.connect(DSN) as db:
        receipt=confirm_local_transfer(db,d["tokens"][d["intake"]],org,src,la,local,local_key,1)
        assert receipt["replayed"] is False
    policy(http,d,org,src,"WAREHOUSE","WRITE")
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockDenied):
            confirm_local_transfer(db,d["tokens"][d["intake"]],org,src,la,local,local_key,1)
        assert balance(db,sa)==(3,0) and balance(db,sb)==(2,0)
        assert allocated(db,la)==2 and allocated(db,local)==1
        assert allocated(db,lb)==2
