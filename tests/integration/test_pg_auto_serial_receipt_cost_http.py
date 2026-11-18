"""PG17 end-to-end: independent serial receipt proof -> movements -> closed costs."""
from io import StringIO
import csv
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_serial_receipt_evidence import assert_receipt,approve_receipt
from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied
from test_pg_baseline import DSN
from test_pg_auto_closed_order_materials_http import fixture
from test_pg_auto_internal_order_costs_http import setup
from test_pg_auto_work_order_estimate_http import token


def test_serial_from_two_receipts_exact_price_lifecycle_csv_and_acl(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode="serial")
    client=TestClient(app,base_url="https://127.0.0.1")
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/warehouse/serial-receipts"
    assert client.get(base).status_code==403
    token(client,d,d["client_a"])
    assert client.get(base).status_code==403
    token(client,d,d["intake"])
    listed=client.get(base)
    assert listed.status_code==200,listed.text
    assert len(listed.json()["items"])==5
    assert all(x["approvalId"] and x["syntheticOnly"] is True for x in listed.json()["items"])
    assert "unitNetMinor" not in listed.text and "supplier" not in listed.text
    serial=evidence["serials"][0]
    body={"serialId":str(serial),"receiptId":str(evidence["receipt"]),
          "requestKey":str(uuid4())}
    # It was already linked before the movement, so a new request is not idempotent.
    assert client.post(base,json=body,headers=token(client,d,d["intake"])).status_code==409
    result=client.get(path,params=query)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,movementCount=6,materialNetMinor=5750)
    group=data["items"][0]["groups"][0]
    assert group["status"]=="SERIAL_TRACED" and group["source"] is None
    assert group["netQuantity"]==4 and group["materialNetMinor"]==5750
    assert group["fifoSlices"]==[]
    slices=group["serialSlices"]
    assert len(slices)==6
    assert sorted((s["action"],s["unitNetMinor"]) for s in slices)==[
        ("ISSUE",1250),("ISSUE",1250),("ISSUE",1250),
        ("ISSUE",2000),("ISSUE",2000),("RETURN",2000)]
    assert next(s for s in slices if s["action"]=="RETURN")["originalIssueId"]==str(evidence["issue"])
    assert {s["serialId"] for s in slices}=={str(x) for x in evidence["serials"]}
    assert {s["sourceLocationId"] for s in slices}=={str(loc)}
    assert all(s["serialMovementId"] for s in slices)
    assert str(d["client_a"]) not in result.text
    csv_result=client.get(path+"/export",params=query)
    assert csv_result.status_code==200
    rows=list(csv.reader(StringIO(csv_result.text.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==15 and all(len(x)==23 for x in rows)
    source_rows=[r for r in rows if r[3]=="SERIAL_SLICE"]
    assert sorted(int(x[10]) for x in source_rows)==[-2000,1250,1250,1250,2000,2000]
    assert {x[-3] for x in source_rows}=={str(x) for x in evidence["serials"]}
    assert {x[-1] for x in source_rows}=={str(loc)}
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""",(org,d["intake"]))
    assert client.get(path,params=query).status_code==403
    assert client.get(path+"/export",params=query).status_code==403


def test_serial_source_missing_second_signoff_never_invents_price(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode="serial-unapproved")
    client=TestClient(app,base_url="https://127.0.0.1")
    token(client,d,d["intake"])
    res=client.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["items"][0]["groups"][0]["status"]=="UNPROVEN"
    assert data["summary"]["materialNetMinor"] is None
    assert data["items"][0]["groups"][0]["serialSlices"]==[]
    with psycopg.connect(DSN) as db:
        assertion=db.execute("""SELECT a.id FROM auto_stock_serial_receipt_assertion a
              WHERE a.serial_id=%s""",(evidence["serials"][-1],)).fetchone()[0]
        with pytest.raises(AutoStockConflict):
            approve_receipt(db,d["tokens"][d["other"]],org,loc,assertion,uuid4())
    assert client.get(path,params=query).json()["summary"]["materialNetMinor"] is None


@pytest.mark.parametrize("mode,status",[("serial-unvalued","UNVALUED"),
                                        ("serial-currency","OTHER_CURRENCY")])
def test_serial_unvalued_or_non_rub_never_zero(monkeypatch,mode,status):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode=mode)
    client=TestClient(app,base_url="https://127.0.0.1")
    token(client,d,d["intake"])
    result=client.get(path,params=query)
    assert result.status_code==200,result.text
    assert result.json()["items"][0]["groups"][0]["status"]==status
    assert result.json()["summary"]["materialNetMinor"] is None


def test_serial_cost_in_combined_labor_report(monkeypatch):
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch,mode="serial")
    client=TestClient(app,base_url="https://127.0.0.1")
    token(client,d,d["intake"])
    path=root+"/reports/internal-order-costs"
    res=client.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,laborEntryCount=1,
        approvedTotalMinor=2500,materialNetMinor=5750,laborCostMinor=1501,
        directCostMinor=7251,syntheticDifferenceMinor=-4751)
    assert data["items"][0]["materialGroups"][0]["status"]=="SERIAL_TRACED"
    rows=list(csv.reader(StringIO(client.get(path+"/export",params=query).text.lstrip("\ufeff")),
                         delimiter=";"))
    assert len(rows)==16 and all(len(r)==30 for r in rows)
    assert len([r for r in rows if r[2]=="SERIAL_SLICE"])==6


def test_serial_evidence_http_csrf_independence_replay_and_immutable(monkeypatch):
    from roboport_api.auto_stock_serial import register_lot_serial
    from roboport_api.auto_stock_receipt_document import create_receipt_document,accept_receipt_document
    from test_pg_auto_stock_bin_lot import add_bin,add_lot
    from test_pg_auto_stock_ledger import sku
    from test_pg_auto_stock_service import grant
    from test_pg_auto_vehicle_claim_http import _scenario
    from test_pg_auto_stock_lot_allocation import setup
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,grant_id=setup(db,d,2)
        from test_pg_auto_stock_service import grant
        grant(db,d["manager"],org,loc,"WAREHOUSE")
        grant(db,d["other"],org,loc,"MANAGER")
        from test_pg_auto_stock_lot_allocation import act
        # Allocation is created solely through independent accepted receipt.
        maker=d["tokens"][d["manager"]]
        acceptor=d["tokens"][d["intake"]]
        receipt=create_receipt_document(db,maker,org,loc,lot,uuid4(),2,"SRC-SERIAL-EVIDENCE")
        accept_receipt_document(db,acceptor,org,loc,receipt,uuid4())
        serial=register_lot_serial(db,acceptor,org,loc,lot,uuid4(),"SERIAL-PROOF-TEST")
    client=TestClient(app,base_url="https://127.0.0.1")
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/warehouse/serial-receipts"
    key=uuid4()
    body={"requestKey":str(key),"serialId":str(serial),"receiptId":str(receipt)}
    headers=token(client,d,d["intake"])
    assert client.post(base,json=body,headers={h:v for h,v in headers.items() if h!="X-CSRF-Token"}).status_code==403
    assert client.post(base,json={**body,"unexpected":1},headers=headers).status_code==422
    response=client.post(base,json=body,headers=headers)
    assert response.status_code==201,response.text
    att=response.json()["assertionId"]
    assert client.post(base,json=body,headers=headers).status_code==200
    assert client.post(base,json={**body,"serialId":str(uuid4())},headers=headers).status_code==403
    assert client.post(base,json={**body,"receiptId":str(uuid4())},headers=headers).status_code==409
    assert client.post(base+"/"+att+"/approve",json={"requestKey":str(uuid4())},headers=headers).status_code==403
    approver=token(client,d,d["other"])
    approval={"requestKey":str(uuid4())}
    assert client.post(base+"/"+att+"/approve",json=approval,headers=approver).status_code==201
    assert client.post(base+"/"+att+"/approve",json=approval,headers=approver).status_code==200
    assert client.post(base+"/"+att+"/approve",json={"requestKey":str(uuid4())},headers=approver).status_code==409
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_stock_serial_receipt_assertion SET receipt_id=%s WHERE id=%s",
                           (uuid4(),UUID(att)))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_stock_serial_receipt_approval SET approved_by=%s WHERE assertion_id=%s",
                           (d["intake"],UUID(att)))
    assert client.get(base).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code IN ('WAREHOUSE','MANAGER')
            AND revoked_at IS NULL""",(org,d["intake"]))
    token(client,d,d["intake"])
    assert client.get(base).status_code==403
    assert client.post(base,json=body,headers=token(client,d,d["intake"])).status_code==403
