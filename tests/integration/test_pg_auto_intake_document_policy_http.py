"""PG17/HTTPS: INTAKE_ACT policy across direct and technical order routes."""
from uuid import uuid4
import psycopg

from test_pg_baseline import DSN
from test_pg_auto_document_access_http import admin, set_rule, url
from test_pg_auto_diagnostic_estimate_http import setup, finding, diagnostic_url
from test_pg_auto_workshop_qc_http import (
    scenario, client_setup, token_as, target, inspection,
)
from test_pg_auto_work_order_estimate_http import token


def rule(http,d,org,loc,role,cap,allowed=False,cutoff=None,user=None):
    owner=token(http,d,d["other"])
    response=set_rule(http,url(org),owner,role=role,loc=loc,user=user,
                      document="INTAKE_ACT",cap=cap,allowed=allowed,
                      not_before=cutoff)
    assert response.status_code==201,response.text


def test_customer_inspection_diagnostics_direct_urls_and_replay(monkeypatch):
    d,org,loc,order,service,_,http=setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    read,inspect,_=target(org,loc,order)
    diagnostics=diagnostic_url(org,loc,order)
    customer=read.removesuffix("/inspection-qc")+"/customer"
    staff=token(http,d,d["intake"])
    assert http.get(customer).status_code==200
    assert http.get(read).status_code==200
    rule(http,d,org,loc,"INTAKE","CREATE")
    staff=token(http,d,d["intake"])
    assert http.post(inspect,headers=staff,json=inspection()).status_code==403
    rule(http,d,org,loc,"INTAKE","CREATE",True)
    staff=token(http,d,d["intake"])
    assert http.post(inspect,headers=staff,json=inspection()).status_code==201
    rule(http,d,org,loc,"INTAKE","READ")
    rule(http,d,org,loc,"INTAKE","READ",True,user=d["intake"])
    staff=token(http,d,d["intake"])
    assert http.get(customer).status_code==403
    assert http.get(read).status_code==403
    assert http.get(diagnostics).status_code==403
    assert http.post(inspect,headers=staff,json=inspection()).status_code==403
    rule(http,d,org,loc,"INTAKE","READ",True,"2099-01-01T00:00:00Z")
    staff=token(http,d,d["intake"])
    assert http.get(customer).status_code==403
    assert http.get(read).status_code==403
    rule(http,d,org,loc,"INTAKE","READ",True)
    staff=token(http,d,d["intake"])
    assert http.get(customer).status_code==200
    assert http.post(inspect,headers=staff,json=inspection()).status_code==200

    payload=finding(service)
    rule(http,d,org,loc,"MECHANIC","WRITE")
    mechanic=token(http,d,d["manager"])
    assert http.get(diagnostics).status_code==200
    assert http.post(diagnostics,headers=mechanic,json=payload).status_code==403
    rule(http,d,org,loc,"MECHANIC","WRITE",True)
    mechanic=token(http,d,d["manager"])
    assert http.post(diagnostics,headers=mechanic,json=payload).status_code==201
    rule(http,d,org,loc,"MECHANIC","READ")
    mechanic=token(http,d,d["manager"])
    assert http.get(diagnostics).status_code==403
    assert http.post(diagnostics,headers=mechanic,json=payload).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_diagnostic WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_work_order_inspection WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)


def test_qc_queue_cutoff_write_deny_and_replay(monkeypatch):
    d,org,loc,_,order,*_=scenario()
    http=client_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    read,inspect,qc=target(org,loc,order)
    queue=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/inspection-qc/pending?limit=1"
    staff=token_as(http,d,d["intake"])
    assert http.post(inspect,headers=staff,json=inspection()).status_code==201
    mechanic=token_as(http,d,d["manager"])
    assert http.get(queue).json()["items"][0]["orderId"]==str(order)
    rule(http,d,org,loc,"MECHANIC","READ",True,"2099-01-01T00:00:00Z")
    mechanic=token_as(http,d,d["manager"])
    page=http.get(queue)
    assert page.status_code==200,page.text
    assert page.json()["items"]==[] and page.json()["nextOrderId"] is None
    assert http.get(read).status_code==403
    rule(http,d,org,loc,"MECHANIC","READ",True)
    rule(http,d,org,loc,"MECHANIC","WRITE")
    mechanic=token_as(http,d,d["manager"])
    payload={"requestKey":str(uuid4()),"result":"FAIL","findingCode":"REWORK"}
    assert http.post(qc,headers=mechanic,json=payload).status_code==403
    rule(http,d,org,loc,"MECHANIC","WRITE",True)
    mechanic=token_as(http,d,d["manager"])
    failed=http.post(qc,headers=mechanic,json=payload)
    assert failed.status_code==201,failed.text
    rule(http,d,org,loc,"MECHANIC","READ")
    mechanic=token_as(http,d,d["manager"])
    assert http.get(queue).status_code==403
    assert http.post(qc,headers=mechanic,json=payload).status_code==403
    rule(http,d,org,loc,"MECHANIC","READ",True)
    rule(http,d,org,loc,"MECHANIC","WRITE")
    worker=token_as(http,d,d["intake"])
    rework={"requestKey":str(uuid4()),"failEventId":failed.json()["qcEvents"][-1]["id"],
            "actionCode":"ADJUSTMENT"}
    assert http.post(qc+"/rework",headers=worker,json=rework).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_event WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_rework WHERE work_order_id=%s",
                          (order,)).fetchone()==(0,)
