"""PG17/HTTPS: stock order and serial mutations enforce live document policy."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_service import AutoStockDenied
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup
from test_pg_auto_stock_interbranch_transfer import setup as transfer_setup
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_serial_movement_http import post
from test_pg_auto_document_access_http import admin, set_rule, url
from test_pg_auto_work_order_estimate_http import api, token


def policy(http,d,org,loc,role,cap,allowed=False,cutoff=None,document="SUPPLIER_ORDER"):
    owner=token(http,d,d["other"])
    response=set_rule(http,url(org),owner,loc=loc,role=role,cap=cap,
        allowed=allowed,not_before=cutoff,document=document)
    assert response.status_code==201,response.text


def test_order_issue_return_handoff_and_reservation_policy(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,4)
        grant(db,d["manager"],org,loc,"MECHANIC")
        admin(db,org,d["other"])
    root=f"/api/v1/dev/auto/organizations/{org}/warehouse/work-orders"
    body={"locationId":str(loc),"workOrderId":str(order),"lotId":str(lot),"quantity":2}
    warehouse=d["intake"]
    mechanic=d["manager"]
    key=uuid4()
    policy(http,d,org,loc,"WAREHOUSE","EDIT")
    assert post(http,d,warehouse,root+"/issue",body,key).status_code==403
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockDenied):
            mutate_work_order_stock(db,d["tokens"][warehouse],org,loc,order,sku,
                                    uuid4(),"RELEASE",1)
        assert allocated(db,lot)==4 and balance(db,sku)==(4,4)
    policy(http,d,org,loc,"WAREHOUSE","EDIT",True)
    issued=post(http,d,warehouse,root+"/issue",body,key)
    assert issued.status_code==201,issued.text
    policy(http,d,org,loc,"WAREHOUSE","READ",True,"2099-01-01T00:00:00Z")
    assert post(http,d,warehouse,root+"/issue",body,key).status_code==403
    assert post(http,d,warehouse,root+"/return",body).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","READ",True)
    ack={"locationId":str(loc),"issueId":issued.json()["issueId"]}
    ack_key=uuid4()
    policy(http,d,org,loc,"MECHANIC","WRITE",document="INTAKE_ACT")
    assert post(http,d,mechanic,root+"/acknowledge",ack,ack_key).status_code==403
    policy(http,d,org,loc,"MECHANIC","WRITE",True,document="INTAKE_ACT")
    approved=post(http,d,mechanic,root+"/acknowledge",ack,ack_key)
    assert approved.status_code==201,approved.text
    policy(http,d,org,loc,"MECHANIC","READ",True,"2099-01-01T00:00:00Z","INTAKE_ACT")
    assert post(http,d,mechanic,root+"/acknowledge",ack,ack_key).status_code==403
    return_key=uuid4()
    returned=post(http,d,warehouse,root+"/return",body,return_key)
    assert returned.status_code==201,returned.text
    policy(http,d,org,loc,"WAREHOUSE","WRITE")
    assert post(http,d,warehouse,root+"/return",body,return_key).status_code==403
    with psycopg.connect(DSN) as db:
        assert allocated(db,lot)==4 and balance(db,sku)==(4,2)
        assert db.execute("SELECT count(*) FROM auto_stock_lot_work_order_issue WHERE work_order_id=%s",(order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_stock_mechanic_handoff WHERE issue_id=%s",
                          (issued.json()["issueId"],)).fetchone()==(1,)


def test_serial_registration_transfer_and_original_scope_replay(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,src,dst,sa,sb,la,lb,_,_=transfer_setup(db,d,2)
        admin(db,org,d["other"])
    root=f"/api/v1/dev/auto/organizations/{org}/warehouse"
    warehouse=d["intake"]
    register={"locationId":str(src),"lotId":str(la),"serialNumber":"POLICY-SERIAL-001"}
    register_key=uuid4()
    policy(http,d,org,src,"WAREHOUSE","CREATE")
    assert post(http,d,warehouse,root+"/serials",register,register_key).status_code==403
    policy(http,d,org,src,"WAREHOUSE","CREATE",True)
    created=post(http,d,warehouse,root+"/serials",register,register_key)
    assert created.status_code==201,created.text
    policy(http,d,org,src,"WAREHOUSE","CREATE")
    assert post(http,d,warehouse,root+"/serials",register,register_key).status_code==403
    payload={"locationId":str(src),"serialId":created.json()["serialId"],"destinationLotId":str(lb)}
    transfer=root+"/serial-movements/transfer"
    key=uuid4()
    policy(http,d,org,dst,"WAREHOUSE","EDIT")
    assert post(http,d,warehouse,transfer,payload,key).status_code==403
    policy(http,d,org,dst,"WAREHOUSE","EDIT",True)
    moved=post(http,d,warehouse,transfer,payload,key)
    assert moved.status_code==201,moved.text
    policy(http,d,org,dst,"WAREHOUSE","READ")
    assert post(http,d,warehouse,transfer,payload,key).status_code==403
    assert post(http,d,warehouse,transfer,{**payload,"serialId":str(uuid4())},key).status_code==403
    policy(http,d,org,dst,"WAREHOUSE","READ",True)
    assert post(http,d,warehouse,transfer,payload,key).json()==moved.json()
    with psycopg.connect(DSN) as db:
        assert allocated(db,la)==1 and allocated(db,lb)==1
        assert balance(db,sa)==(1,0) and balance(db,sb)==(1,0)
        assert db.execute("SELECT lot_id FROM auto_stock_serial_current WHERE serial_id=%s",
                          (created.json()["serialId"],)).fetchone()==(lb,)


def test_serial_issue_and_return_cannot_bypass_edit_or_history(monkeypatch):
    http=api(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,1)
        admin(db,org,d["other"])
    root=f"/api/v1/dev/auto/organizations/{org}/warehouse"
    warehouse=d["intake"]
    registered=post(http,d,warehouse,root+"/serials",{"locationId":str(loc),
        "lotId":str(lot),"serialNumber":"POLICY-SERIAL-ORDER"})
    assert registered.status_code==201,registered.text
    body={"locationId":str(loc),"serialId":registered.json()["serialId"],"workOrderId":str(order)}
    key=uuid4()
    policy(http,d,org,loc,"WAREHOUSE","EDIT")
    assert post(http,d,warehouse,root+"/serial-movements/issue",body,key).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","EDIT",True)
    issued=post(http,d,warehouse,root+"/serial-movements/issue",body,key)
    assert issued.status_code==201,issued.text
    policy(http,d,org,loc,"WAREHOUSE","READ",True,"2099-01-01T00:00:00Z")
    assert post(http,d,warehouse,root+"/serial-movements/issue",body,key).status_code==403
    assert post(http,d,warehouse,root+"/serial-movements/return",body).status_code==403
    policy(http,d,org,loc,"WAREHOUSE","READ",True)
    returned=post(http,d,warehouse,root+"/serial-movements/return",body)
    assert returned.status_code==201,returned.text
    with psycopg.connect(DSN) as db:
        assert allocated(db,lot)==1 and balance(db,sku)==(1,0)
