"""Real PG17: receipt -> net issue/return -> QC -> document closure -> material report."""
from datetime import datetime,timezone
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo
import csv
from io import StringIO

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api import auto_closed_order_materials as module
from roboport_api.main import app
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document,accept_receipt_document,
)
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from roboport_api.auto_work_order_execution import issue_material,receive_material
from roboport_api.auto_document_lifecycle import record_document_event
from test_pg_auto_document_lifecycle_http import ready
from test_pg_auto_document_access_http import admin,set_rule,url as policy_url
from test_pg_auto_work_order_estimate_http import token
from test_pg_auto_stock_ledger import sku,record
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import act as lot_action
from test_pg_auto_work_order_stock import act as work_stock
from test_pg_auto_stock_lot_work_order_return import ret
from test_pg_baseline import DSN


def fixture(monkeypatch,mode="traced",before_labor=None):
    evidence={}
    def setup(db,d,org,loc,order):
        item=sku(db,org,loc)
        lot=add_lot(db,org,loc,item,add_bin(db,org,loc))
        warehouse=d["tokens"][d["intake"]]
        # A genuinely new lot, populated only by an independently accepted receipt.
        manager=d["tokens"][d["intake"]]
        # Receipt maker must not be its accepting manager.
        from test_pg_auto_stock_service import grant
        grant(db,d["manager"],org,loc,"WAREHOUSE")
        maker=d["tokens"][d["manager"]]
        receipt=create_receipt_document(db,maker,org,loc,lot,uuid4(),3,"SYN-COST-RECEIPT")
        acceptance=accept_receipt_document(db,manager,org,loc,receipt,uuid4())
        valuation=None
        if mode!="unvalued":
            valuation=record_receipt_valuation(
                db,manager,org,loc,receipt,uuid4(),
                "USD" if mode=="currency" else "RUB",1250,600,
                "SYN-QUOTE-1",datetime.now(timezone.utc))
        if mode.startswith("fifo") or mode.startswith("serial"):
            second=create_receipt_document(db,maker,org,loc,lot,uuid4(),3,"SYN-FIFO-SECOND")
            second_accept=accept_receipt_document(db,manager,org,loc,second,uuid4())
            second_value=None
            if mode not in ("fifo-unvalued","serial-unvalued"):
                second_value=record_receipt_valuation(db,manager,org,loc,second,uuid4(),
                    "USD" if mode in ("fifo-currency","serial-currency") else "RUB",2000,600,"SYN-QUOTE-2",
                    datetime.now(timezone.utc))
            evidence.update(second=second,second_accept=second_accept,second_value=second_value)
            if mode=="fifo-serial":
                from roboport_api.auto_stock_serial import register_lot_serial
                register_lot_serial(db,warehouse,org,loc,lot,uuid4(),"SYN-FIFO-SERIAL")
        if mode.startswith("serial"):
            from roboport_api.auto_stock_serial import register_lot_serial
            from roboport_api.auto_serial_receipt_evidence import assert_receipt,approve_receipt
            from test_pg_auto_stock_service import grant
            grant(db,d["other"],org,loc,"MANAGER")
            identities=[]
            for i in range(5):
                serial=register_lot_serial(db,warehouse,org,loc,lot,uuid4(),f"SERIAL-COST-{uuid4().hex[:15]}")
                source=receipt if i<3 else evidence["second"]
                _,attested=assert_receipt(db,warehouse,org,loc,serial,source,uuid4())
                if mode!="serial-unapproved" or i<4:
                    approve_receipt(db,d["tokens"][d["other"]],org,loc,
                                    UUID(attested["assertionId"]),uuid4())
                identities.append(serial)
            evidence["serials"]=identities
        if mode=="mixed":
            record(db,item,"RECEIPT",1)
            lot_action(db,d,org,loc,lot,"ALLOCATE",1)
        if mode=="unpaired":
            record(db,item,"RECEIPT",1)
        work_stock(db,d,org,loc,order,item,"RESERVE",5 if mode.startswith(("fifo","serial")) else (3 if mode=="unpaired" else 2))
        evidence.update(item=item,lot=lot,receipt=receipt,acceptance=acceptance,valuation=valuation)
        if before_labor is not None:
            before_labor(db,d,org,loc,order)
        def consume():
            if mode.startswith("serial"):
                from roboport_api.auto_stock_serial_movement import move_lot_serial
                issue_ids=[]
                for serial_id in evidence["serials"]:
                    movement=move_lot_serial(db,warehouse,org,loc,serial_id,uuid4(),"ISSUE",order)
                    issue_id=db.execute("SELECT issue_id FROM auto_stock_serial_movement WHERE id=%s",
                                        (movement,)).fetchone()[0]
                    receive_material(db,d["tokens"][d["manager"]],org,loc,order,issue_id,uuid4())
                    issue_ids.append(issue_id)
                move_lot_serial(db,warehouse,org,loc,evidence["serials"][-1],uuid4(),"RETURN",order)
                evidence["issue"]=issue_ids[-1]
                evidence["issuedIds"]=issue_ids
                return
            issue_key=uuid4()
            quantity=5 if mode.startswith("fifo") else 2
            issued=issue_material(db,warehouse,org,loc,order,lot,quantity,issue_key)
            ident=UUID(issued["issueId"])
            assert issue_material(db,warehouse,org,loc,order,lot,quantity,issue_key)["issueId"]==str(ident)
            receive_material(db,d["tokens"][d["manager"]],org,loc,order,ident,uuid4())
            return_key=uuid4()
            returned=ret(db,d,org,loc,order,lot,1,return_key)
            assert ret(db,d,org,loc,order,lot,1,return_key)==returned
            if mode=="unpaired":
                work_stock(db,d,org,loc,order,item,"ISSUE",1)
            evidence.update(issue=ident,returned=returned)
        return consume
    d,org,loc,order,act,preview=ready(monkeypatch,material_setup=setup)
    with psycopg.connect(DSN) as db:
        for action,actor in (("POSTED",d["intake"]),("CLIENT_ACK",d["client_a"]),
                             ("CLOSED_TECHNICAL",d["intake"])):
            record_document_event(
                db,d["tokens"][actor],org,loc,order,request_key=uuid4(),action=action,
                preview_id=UUID(preview["id"]),preview_digest=preview["digest"],
                intake_act_id=UUID(act["id"]),intake_digest=act["sha256"])
        admin(db,org,d["other"])
        stamp,zone=db.execute("""
          SELECT e.created_at,l.timezone_name FROM auto_order_demo_document_event e
          JOIN auto_work_order w ON w.id=e.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'
        """,(order,)).fetchone()
    day=stamp.astimezone(ZoneInfo(zone)).date().isoformat()
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-order-materials"
    return d,org,loc,order,evidence,path,{"fromDay":day,"toDay":day}


def test_exact_single_receipt_net_cost_and_live_price_scope(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    assert http.get(path,params=query).status_code==403
    token(http,d,d["client_a"])
    assert http.get(path,params=query).status_code==403
    token(http,d,d["intake"])
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,movementCount=2,materialNetMinor=1250)
    item=data["items"][0]
    assert item["workOrderId"]==str(order) and item["status"]=="COMPLETE"
    group=item["groups"][0]
    assert group["status"]=="TRACED"
    assert group["issuedQuantity"]==2 and group["returnedQuantity"]==1 and group["netQuantity"]==1
    assert group["materialNetMinor"]==1250
    assert group["source"]==dict(receiptId=str(evidence["receipt"]),
      acceptanceId=str(evidence["acceptance"]),valuationId=str(evidence["valuation"]),unitNetMinor=1250)
    assert {m["lotMovementId"] for m in group["movements"]}=={
        str(evidence["issue"]),str(evidence["returned"])}
    assert str(d["client_a"]) not in res.text and str(d["vehicle"]) not in res.text
    assert data["marginAvailable"] is False and data["payrollAvailable"] is False
    export=http.get(path+"/export",params=query)
    assert export.status_code==200 and export.headers["x-content-type-options"]=="nosniff"
    lines=list(csv.reader(StringIO(export.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==5 and all(len(row)==23 for row in lines)
    assert lines[1][3:5]==["ORDER","COMPLETE"] and lines[1][10]=="1250"
    assert lines[2][3:5]==["LOT","TRACED"] and lines[2][14]=="1250"
    assert http.get(path.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(path,params={"fromDay":"2026-02-31","toDay":query["toDay"]}).status_code==422
    monkeypatch.setattr(module,"MAX_ALLOCATIONS",2)
    assert http.get(path,params=query).status_code==409
    assert http.get(path+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_ALLOCATIONS",5000)
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="SUPPLIER_ORDER",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


@pytest.mark.parametrize("mode,status",[
    ("mixed","UNPROVEN"),("unvalued","UNVALUED"),("currency","OTHER_CURRENCY"),
])
def test_unknown_or_non_rub_cost_never_becomes_zero(monkeypatch,mode,status):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]["materialNetMinor"] is None
    assert data["summary"]["completeOrderCount"]==0
    assert data["items"][0]["status"]=="INCOMPLETE"
    assert data["items"][0]["groups"][0]["status"]==status
    assert data["items"][0]["groups"][0]["source"] is None
    assert data["items"][0]["groups"][0]["materialNetMinor"] is None
    if mode=="unvalued":
        with psycopg.connect(DSN) as db:
            record_receipt_valuation(db,d["tokens"][d["intake"]],org,loc,evidence["receipt"],
                uuid4(),"RUB",1250,600,"SYN-LATE-QUOTE",datetime.now(timezone.utc))
        retry=http.get(path,params=query)
        assert retry.status_code==200,retry.text
        assert retry.json()["items"][0]["groups"][0]["status"]=="LATE_VALUATION"
        assert retry.json()["summary"]["materialNetMinor"] is None


def test_unpaired_movement_prevents_complete_order_cost(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,"unpaired")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=0,
                                 movementCount=3,materialNetMinor=None)
    item=data["items"][0]
    assert item["status"]=="INCOMPLETE" and item["materialNetMinor"] is None
    assert {group["status"] for group in item["groups"]}=={"UNPAIRED","TRACED"}
    unpaired=next(group for group in item["groups"] if group["status"]=="UNPAIRED")
    assert unpaired["lotId"] is None and unpaired["source"] is None
    assert unpaired["netQuantity"]==1 and unpaired["materialNetMinor"] is None


def test_service_only_order_has_zero_registered_material_usage(monkeypatch):
    from test_pg_auto_mechanic_productivity_http import _close
    d,org,loc,order=_close(monkeypatch)
    with psycopg.connect(DSN) as db:
        stamp,zone=db.execute("""
          SELECT e.created_at,l.timezone_name FROM auto_order_demo_document_event e
          JOIN auto_work_order w ON w.id=e.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'
        """,(order,)).fetchone()
    day=stamp.astimezone(ZoneInfo(zone)).date().isoformat()
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-order-materials",
                      params={"fromDay":day,"toDay":day})
    assert response.status_code==200,response.text
    assert response.json()["summary"]==dict(orderCount=1,completeOrderCount=1,
                                           movementCount=0,materialNetMinor=0)
    assert response.json()["items"][0]["groups"]==[]


def test_policy_epoch_rejects_repeatable_read_snapshot_older_than_new_deny(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch)
    # Снимок читателя старше конкурентной публикации. Блокировка org,
    # полученная позже, должна отклонить старый снимок, а не раскрыть цены.
    from roboport_api.auto_closed_order_report import report as closed_report
    http=TestClient(app,base_url="https://127.0.0.1")
    with psycopg.connect(DSN) as reader:
        with pytest.raises(psycopg.errors.SerializationFailure):
            with reader.transaction():
                reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                epoch=reader.execute("""SELECT document_policy_epoch FROM auto_organization
                   WHERE id=%s""",(org,)).fetchone()[0]
                owner=token(http,d,d["other"])
                assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="SUPPLIER_ORDER",cap="PRICE_PURCHASE").status_code==201
                with psycopg.connect(DSN) as fresh:
                    assert fresh.execute("SELECT document_policy_epoch FROM auto_organization WHERE id=%s",
                                         (org,)).fetchone()[0]==epoch+1
                closed_report(reader,d["tokens"][d["intake"]],org,loc,query["fromDay"],query["toDay"])
    token(http,d,d["intake"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


def test_older_transaction_timestamp_cannot_backdate_cost_registration(monkeypatch):
    # now() старой транзакции предшествует выдаче; регистрация оценки
    # происходит после выдачи в другой committed транзакции.
    with psycopg.connect(DSN) as older:
        started=older.execute("SELECT now()").fetchone()[0]
        d,org,loc,order,evidence,path,query=fixture(monkeypatch,"unvalued")
        ident=record_receipt_valuation(older,d["tokens"][d["intake"]],org,loc,evidence["receipt"],
           uuid4(),"RUB",1250,600,"SYN-OLDER-TX",datetime.now(timezone.utc))
        older.commit()
    with psycopg.connect(DSN) as db:
        row=db.execute("""SELECT v.created_at,v.cost_registration_seq,a.created_at,a.cost_registration_seq
          FROM auto_stock_receipt_valuation v
          JOIN auto_stock_lot_work_order_issue i ON i.id=%s
          JOIN auto_stock_lot_allocation a ON a.id=i.lot_debit_id WHERE v.id=%s
        """,(evidence["issue"],ident)).fetchone()
        assert row[0]==started and row[0]<row[2] and row[1]>row[3]
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    assert result.json()["items"][0]["groups"][0]["status"]=="LATE_VALUATION"
    assert result.json()["summary"]["materialNetMinor"] is None


def test_fifo_two_accepted_receipts_return_reverses_expensive_layer_and_csv(monkeypatch):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,"fifo")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,movementCount=2,materialNetMinor=5750)
    row=data["items"][0]["groups"][0]
    assert row["status"]=="FIFO_TRACED" and row["source"] is None
    slices=row["fifoSlices"]
    assert len(slices)==3
    assert {(s["action"],s["receiptId"],s["quantity"],s["unitNetMinor"]) for s in slices}=={
        ("ISSUE",str(evidence["receipt"]),3,1250),
        ("ISSUE",str(evidence["second"]),2,2000),
        ("RETURN",str(evidence["second"]),1,2000)}
    assert {s["originalIssueId"] for s in slices}=={str(evidence["issue"])}
    assert row["netQuantity"]==4 and row["materialNetMinor"]==5750
    export=http.get(path+"/export",params=query)
    assert export.status_code==200,export.text
    lines=list(csv.reader(StringIO(export.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==8 and all(len(line)==23 for line in lines)
    fifo_rows=[line for line in lines if line[3]=="FIFO_SLICE"]
    assert sorted(int(line[10]) for line in fifo_rows)==[-2000,3750,4000]
    assert {line[-4] for line in fifo_rows}=={str(evidence["issue"])}
    monkeypatch.setattr(module.fifo,"MAX_SLICES",2)
    assert http.get(path,params=query).status_code==409
    assert http.get(path+"/export",params=query).status_code==409


@pytest.mark.parametrize("mode,status",[("fifo-unvalued","UNVALUED"),("fifo-currency","OTHER_CURRENCY"),
                                        ("fifo-serial","UNPROVEN")])
def test_fifo_unknown_provenance_never_invents_cost(monkeypatch,mode,status):
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    group=res.json()["items"][0]["groups"][0]
    assert group["status"]==status and group["materialNetMinor"] is None
    assert group["fifoSlices"]==[]
    assert res.json()["summary"]["materialNetMinor"] is None
