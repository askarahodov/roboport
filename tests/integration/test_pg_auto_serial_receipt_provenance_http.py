"""Real PG17 serial receipt attestation, exact material prices, API/ACL and lifecycle."""
from io import StringIO
import csv
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api import auto_serial_receipt_provenance as provenance
from test_pg_auto_closed_order_materials_http import fixture
from test_pg_auto_internal_order_costs_http import setup
from test_pg_auto_work_order_estimate_http import token
from test_pg_auto_document_access_http import set_rule,url as policy_url
from test_pg_baseline import DSN


def test_serial_receipt_costs_match_exact_approved_sources_and_both_exports(monkeypatch):
    d,org,loc,order,proof,path,query=fixture(monkeypatch,"serial")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,
                                 movementCount=3,materialNetMinor=1250)
    group=data["items"][0]["groups"][0]
    assert group["status"]=="SERIAL_TRACED" and group["source"] is None
    assert group["issuedQuantity"]==2 and group["returnedQuantity"]==1
    assert group["materialNetMinor"]==1250
    slices=group["fifoSlices"]
    assert len(slices)==3
    assert {(s["serialId"],s["action"],s["unitNetMinor"]) for s in slices}=={
        (str(proof["serial1"]),"ISSUE",1250),
        (str(proof["serial2"]),"ISSUE",2000),
        (str(proof["serial2"]),"RETURN",2000)}
    assert {s["serialApprovalId"] for s in slices}=={
        proof["a1"]["approvalId"],proof["a2"]["approvalId"]}
    assert {s["originalIssueId"] for s in slices if s["action"]=="RETURN"}=={
        str(proof["serial2Issue"])}
    assert str(d["client_a"]) not in response.text
    out=http.get(path+"/export",params=query)
    assert out.status_code==200
    rows=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==9 and all(len(row)==22 for row in rows)
    source_rows=[r for r in rows if r[3]=="FIFO_SLICE"]
    assert sorted(int(r[10]) for r in source_rows)==[-2000,1250,2000]
    assert {r[-2] for r in source_rows}=={str(proof["serial1"]),str(proof["serial2"])}
    assert {r[-1] for r in source_rows}=={
        proof["a1"]["approvalId"],proof["a2"]["approvalId"]}
    policy=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/serial-receipt-provenance"
    assert http.get(policy).status_code==200
    assert len(http.get(policy).json()["items"])==2
    assert http.get(policy.replace(str(loc),str(uuid4()))).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="SUPPLIER_ORDER",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(policy).status_code==403
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


@pytest.mark.parametrize("mode,status",[
    ("serial-unapproved","UNPROVEN"),("serial-unvalued","UNVALUED"),
    ("serial-currency","OTHER_CURRENCY"),
])
def test_unapproved_or_unvalued_serial_is_never_implicitly_priced(monkeypatch,mode,status):
    d,org,loc,order,proof,path,query=fixture(monkeypatch,mode)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    assert response.json()["items"][0]["groups"][0]["status"]==status
    assert response.json()["summary"]["materialNetMinor"] is None
    assert response.json()["items"][0]["groups"][0]["fifoSlices"]==[]


def test_serial_source_api_idempotency_dual_control_and_immutable_sql(monkeypatch):
    d,org,loc,order,proof,path,query=fixture(monkeypatch,"serial-unapproved")
    http=TestClient(app,base_url="https://127.0.0.1")
    prefix=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/serial-receipt-provenance"
    warehouse=token(http,d,d["manager"])
    assert http.get(prefix).status_code==403
    payload={"requestKey":str(uuid4()),"serialId":str(proof["serial1"]),
             "receiptId":str(proof["receipt"])}
    # The old source is already proposed, so a new assertion cannot rewrite it.
    assert http.post(prefix,headers=warehouse,json=payload).status_code==409
    assert http.post(prefix,headers=warehouse,json={**payload,"serialId":"bad"}).status_code==422
    assert http.post(prefix,headers=warehouse,json={**payload,"serialId":str(uuid4())}).status_code==403
    assert http.post(prefix+"/"+proof["p1"]["assertionId"]+"/approve",
                     headers=warehouse,json={"requestKey":str(uuid4())}).status_code==403
    manager=token(http,d,d["intake"])
    assert http.get(prefix).status_code==200
    assert http.post(prefix+"/"+proof["p1"]["assertionId"]+"/approve",
                     headers=manager,json={"requestKey":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        for sql,args in [
            ("UPDATE auto_stock_serial_receipt_assertion SET receipt_id=%s WHERE id=%s",
             (proof["second"],UUID(proof["p1"]["assertionId"]))),
            ("DELETE FROM auto_stock_serial_receipt_assertion WHERE id=%s",
             (UUID(proof["p1"]["assertionId"]),)),
        ]:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,args)
    assert http.get(prefix).json()["items"][0]["approvalId"] is None


def test_sealed_order_uses_serial_cost_but_missing_internal_rate_remains_unknown(monkeypatch):
    d,org,loc,order,proof,root,query,rate=setup(monkeypatch,mode="serial")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(root+"/reports/internal-order-costs",params=query)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,laborEntryCount=1,
      approvedTotalMinor=2500,materialNetMinor=1250,laborCostMinor=1501,
      directCostMinor=2751,syntheticDifferenceMinor=-251)
    assert data["items"][0]["materialGroups"][0]["status"]=="SERIAL_TRACED"
    out=http.get(root+"/reports/internal-order-costs/export",params=query)
    assert out.status_code==200
    rows=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==10 and all(len(r)==29 for r in rows)
    assert len([r for r in rows if r[2]=="FIFO_SLICE"])==3
