"""PG17: exact original purchase price survives serial transfer to another branch.

Real accepted receipts, independently approved serial attribution, serial
transfer/issue/return, mechanic handoff, QC, seal and CLOSED_TECHNICAL.
"""
from datetime import datetime,timezone
from io import StringIO
import csv
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document,accept_receipt_document,
)
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from roboport_api.auto_serial_receipt_provenance import propose,approve
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api.auto_stock_serial_movement import move_lot_serial
from roboport_api.auto_work_order_execution import receive_material
from roboport_api.auto_document_lifecycle import record_document_event
from roboport_api import auto_internal_order_costs as costs
from test_pg_auto_document_lifecycle_http import ready
from test_pg_auto_document_access_http import admin,set_rule,url as policy_url
from test_pg_auto_work_order_estimate_http import token
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_ledger import sku
from test_pg_auto_work_order_stock import act as work_stock
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def lifecycle(monkeypatch,*,mode="valued"):
    evidence={}
    def build(db,d,org,dst,order):
        source=uuid4()
        db.execute("""INSERT INTO auto_service_location(id,code,organization_id)
                      VALUES(%s,%s,%s)""",
                   (source,"SRC_"+source.hex[:8].upper(),org))
        dst_sku=sku(db,org,dst)
        article=db.execute("SELECT article FROM auto_stock_sku WHERE id=%s",
                           (dst_sku,)).fetchone()[0]
        src_sku=uuid4()
        db.execute("""INSERT INTO auto_stock_sku(id,organization_id,location_id,article)
                      VALUES(%s,%s,%s,%s)""",(src_sku,org,source,article))
        src_lot=add_lot(db,org,source,src_sku,add_bin(db,org,source))
        dst_lot=add_lot(db,org,dst,dst_sku,add_bin(db,org,dst))
        for user,role in ((d["manager"],"WAREHOUSE"),(d["intake"],"WAREHOUSE"),
                          (d["intake"],"MANAGER"),(d["other"],"MANAGER")):
            grant(db,user,org,source,role)
        grant(db,d["other"],org,dst,"MANAGER")
        maker=d["tokens"][d["manager"]]
        acceptor=d["tokens"][d["intake"]]
        confirmer=d["tokens"][d["other"]]
        serials=[]
        for i,price in enumerate((1250,2000)):
            receipt=create_receipt_document(db,maker,org,source,src_lot,uuid4(),1,
                        "XBRANCH-PURCHASE-"+str(i))
            accept_receipt_document(db,acceptor,org,source,receipt,uuid4())
            if mode!="unvalued" or i==0:
                record_receipt_valuation(db,acceptor,org,source,receipt,uuid4(),
                    "USD" if mode=="currency" and i==1 else "RUB",price,0,
                    "XBRANCH-QUOTE-"+str(i),datetime.now(timezone.utc))
            serial=register_lot_serial(db,acceptor,org,source,src_lot,uuid4(),
                                      "XBRANCH-SERIAL-"+str(i)+uuid4().hex[:9])
            proposal=propose(db,maker,org,source,serial,receipt,uuid4())
            approved=approve(db,confirmer,org,source,UUID(proposal["assertionId"]),uuid4())
            moved=move_lot_serial(db,acceptor,org,source,serial,uuid4(),"TRANSFER",
                                  None,dst_lot)
            serials.append((serial,receipt,approved["approvalId"],moved))
        work_stock(db,d,org,dst,order,dst_sku,"RESERVE",2)
        rate=costs.propose(db,acceptor,org,dst,uuid4(),0,2001)
        costs.approve(db,confirmer,org,dst,UUID(rate["items"][0]["id"]),uuid4())
        evidence.update(source=source,lot=dst_lot,serials=serials)
        def consume():
            issues=[]
            for serial,_,_,_ in serials:
                moved=move_lot_serial(db,acceptor,org,dst,serial,uuid4(),"ISSUE",
                                      order,None)
                issue=db.execute("""SELECT issue_id FROM auto_stock_serial_movement
                    WHERE id=%s""",(moved,)).fetchone()[0]
                receive_material(db,d["tokens"][d["manager"]],org,dst,order,issue,uuid4())
                issues.append(issue)
            moved=move_lot_serial(db,acceptor,org,dst,serials[-1][0],uuid4(),
                                  "RETURN",order,None)
            evidence.update(issues=issues,returned=moved)
        return consume
    d,org,loc,order,act,preview=ready(monkeypatch,material_setup=build)
    with psycopg.connect(DSN) as db:
        for action,user in (("POSTED",d["intake"]),("CLIENT_ACK",d["client_a"]),
                            ("CLOSED_TECHNICAL",d["intake"])):
            record_document_event(db,d["tokens"][user],org,loc,order,
                request_key=uuid4(),action=action,preview_id=UUID(preview["id"]),
                preview_digest=preview["digest"],intake_act_id=UUID(act["id"]),
                intake_digest=act["sha256"])
        admin(db,org,d["other"])
        timestamp,tz=db.execute("""
           SELECT ev.created_at,loc.timezone_name
           FROM auto_order_demo_document_event ev
           JOIN auto_work_order w ON w.id=ev.work_order_id
           JOIN auto_service_location loc ON loc.id=w.location_id
           WHERE ev.work_order_id=%s AND ev.action='CLOSED_TECHNICAL'
        """,(order,)).fetchone()
    day=timestamp.astimezone(ZoneInfo(tz)).date().isoformat()
    query={"fromDay":day,"toDay":day}
    prefix=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    return d,org,loc,order,evidence,prefix,query


def test_serial_transfer_from_two_approved_source_receipts_to_closed_costs(monkeypatch):
    d,org,dst,order,proof,root,period=lifecycle(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    endpoint=root+"/reports/closed-order-materials"
    assert http.get(endpoint,params=period).status_code==403
    token(http,d,d["intake"])
    res=http.get(endpoint,params=period)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]=={"orderCount":1,"completeOrderCount":1,
                             "movementCount":3,"materialNetMinor":1250}
    group=data["items"][0]["groups"][0]
    assert group["status"]=="SERIAL_TRACED"
    assert group["netQuantity"]==1 and group["materialNetMinor"]==1250
    assert group["source"] is None and len(group["fifoSlices"])==3
    assert {(x["action"],x["unitNetMinor"]) for x in group["fifoSlices"]}=={
        ("ISSUE",1250),("ISSUE",2000),("RETURN",2000)}
    assert {x["sourceLocationId"] for x in group["fifoSlices"]}=={
        str(proof["source"])}
    assert {x["receiptId"] for x in group["fifoSlices"]}=={
        str(x[1]) for x in proof["serials"]}
    assert {x["serialApprovalId"] for x in group["fifoSlices"]}=={
        x[2] for x in proof["serials"]}
    assert {x["originalIssueId"] for x in group["fifoSlices"] if
            x["action"]=="RETURN"}=={str(proof["issues"][-1])}
    assert str(d["client_a"]) not in res.text
    combined=http.get(root+"/reports/internal-order-costs",params=period)
    assert combined.status_code==200,combined.text
    assert combined.json()["summary"]=={
        "orderCount":1,"completeOrderCount":1,"laborEntryCount":1,
        "approvedTotalMinor":2500,"materialNetMinor":1250,"laborCostMinor":1501,
        "directCostMinor":2751,"syntheticDifferenceMinor":-251,
    }
    for route,cols in ((endpoint,23),(root+"/reports/internal-order-costs",30)):
        response=http.get(route+"/export",params=period)
        assert response.status_code==200,response.text
        lines=list(csv.reader(StringIO(response.text.lstrip("\ufeff")),delimiter=";"))
        assert all(len(line)==cols for line in lines)
        assert len([line for line in lines if line[3 if cols==23 else 2]=="FIFO_SLICE"])==3
        assert {line[-1] for line in lines if line[3 if cols==23 else 2]=="FIFO_SLICE"}=={
            str(proof["source"])}
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=proof["source"],user=d["intake"],
                    document="SUPPLIER_ORDER",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    for route in (endpoint,root+"/reports/internal-order-costs"):
        assert http.get(route,params=period).status_code==403
        assert http.get(route+"/export",params=period).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=proof["source"],user=d["intake"],
                    document="SUPPLIER_ORDER",cap="PRICE_PURCHASE",allowed=True).status_code==201
    assert set_rule(http,policy_url(org),owner,loc=proof["source"],user=d["intake"],
                    document="FINANCE",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(endpoint,params=period).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s
            AND role_code='MANAGER' AND revoked_at IS NULL""",
          (org,proof["source"],d["intake"]))
    assert http.get(endpoint,params=period).status_code==403


def test_serial_transfer_unknown_source_does_not_invent_cost(monkeypatch):
    d,org,dst,order,proof,root,period=lifecycle(monkeypatch,mode="unvalued")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    for route in (root+"/reports/closed-order-materials",
                  root+"/reports/internal-order-costs"):
        res=http.get(route,params=period)
        assert res.status_code==200,res.text
        item=res.json()["items"][0]
        group=(item["groups"] if "groups" in item else item["materialGroups"])[0]
        assert group["status"]=="UNVALUED"
        assert group["fifoSlices"]==[]
        assert res.json()["summary"]["materialNetMinor"] is None
    d,org,dst,order,proof,root,period=lifecycle(monkeypatch,mode="currency")
    token(http,d,d["intake"])
    response=http.get(root+"/reports/closed-order-materials",params=period)
    assert response.status_code==200,response.text
    assert response.json()["items"][0]["groups"][0]["status"]=="OTHER_CURRENCY"
    assert response.json()["summary"]["materialNetMinor"] is None
