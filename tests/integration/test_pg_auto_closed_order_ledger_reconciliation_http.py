"""PG17/HTTPS: CLOSED_TECHNICAL documents versus independent treasury and cost."""
from io import StringIO
from uuid import UUID, uuid4
import csv

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api import auto_closed_order_ledger_reconciliation as module
from roboport_api.auto_treasury import create_account, record_event
from roboport_api.main import app
from test_pg_auto_document_access_http import set_rule, url as policy_url
from test_pg_auto_internal_order_costs_http import setup
from test_pg_auto_stock_service import grant
from test_pg_auto_work_order_estimate_http import token
from test_pg_baseline import DSN


def _path(root):
    return root + "/reports/closed-order-ledger-reconciliation"


def _hook(sequence, evidence):
    def hook(db, d, org, loc, order):
        grant(db, d["manager"], org, loc, "CASHIER")
        opened = create_account(
            db, d["tokens"][d["intake"]], org, loc,
            request_key=uuid4(), code="SYN-01", label="Тестовая касса",
            kind="CASH_SIM")
        account = UUID(opened["accountId"])
        evidence["accountId"] = str(account)
        evidence["eventIds"] = []
        for kind, amount in sequence:
            state = record_event(db, d["tokens"][d["manager"]], org, loc, order,
                account_id=account, request_key=uuid4(),
                kind=kind, amount_minor=amount,
                reason="Синтетическая проверка")
            evidence["eventIds"].append(state["eventId"])
    return hook


def test_closed_order_two_independent_ledgers_full_settlement_and_sources(monkeypatch):
    evidence = {}
    hook = _hook([
        ("ADVANCE", 900), ("REFUND_ADVANCE", 200),
        ("APPLY_ADVANCE", 500), ("PAYMENT", 1800),
        ("APPLY_ADVANCE", 200),
    ], evidence)
    d,org,loc,order,material,root,query,rate=setup(
        monkeypatch, before_cash=hook)
    http=TestClient(app,base_url="https://127.0.0.1")
    url=_path(root)
    assert http.get(url,params=query).status_code==403
    token(http,d,d["client_a"])
    assert http.get(url,params=query).status_code==403
    assert http.get(url+"/export",params=query).status_code==403
    token(http,d,d["intake"])
    response=http.get(url,params=query)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    body=response.json()
    assert body["syntheticOnly"] is True
    assert body["realMoney"] is False and body["releaseAllowed"] is False
    assert body["marginAvailable"] is False and body["payrollAvailable"] is False
    assert body["basis"]==module.BASIS
    assert body["summary"]=={
        "orderCount":1,"costCompleteCount":1,"ledgerLinkedCount":1,
        "ledgerSettledCount":1,"treasuryEventCount":5,
        "approvedTotalMinor":"2500","demoNetMinor":"2500",
        "treasurySettledMinor":"2500","unappliedAdvanceMinor":"0",
        "treasuryCashNetMinor":"2500","treasuryDueMinor":"0",
        "demoMinusTreasuryMinor":"0",
    }
    row=body["items"][0]
    assert row["workOrderId"]==str(order)
    assert row["costStatus"]=="COMPLETE" and row["ledgerStatus"]=="SETTLED"
    assert row["directCostMinor"]=="2751"
    assert row["syntheticDifferenceMinor"]=="-251"
    assert row["approvedTotalMinor"]==row["demoNetMinor"]=="2500"
    assert {ev["eventId"] for ev in row["treasuryEvents"]}==set(evidence["eventIds"])
    assert {ev["accountId"] for ev in row["treasuryEvents"]}=={evidence["accountId"]}
    assert row["costEvidence"]["labor"][0]["approvalId"]==rate["approvalId"]
    assert row["costEvidence"]["lots"][0]["valuationId"]==str(material["valuation"])
    with psycopg.connect(DSN) as db:
        source_ids={str(x[0]) for x in db.execute("""
            SELECT id FROM auto_treasury_event WHERE work_order_id=%s
        """,(order,)).fetchall()}
        assert source_ids==set(evidence["eventIds"])
    # No customer fields, VIN, cashier actor, free-text reason or bank details.
    for forbidden in (str(d["client_a"]),str(d["vehicle"]),
                      str(d["manager"]),"Синтетическая проверка"):
        assert forbidden not in response.text
    out=http.get(url+"/export",params=query)
    assert out.status_code==200,out.text
    assert out.headers["cache-control"]=="no-store"
    assert out.headers["x-content-type-options"]=="nosniff"
    assert out.headers["content-type"].startswith("text/csv")
    records=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert all(len(record)==36 for record in records)
    assert len(records)==9
    assert [x[0] for x in records[1:]].count("ORDER")==1
    assert [x[0] for x in records[1:]].count("TREASURY_EVENT")==5
    assert [x[0] for x in records[1:]].count("LABOR_SOURCE")==1
    assert [x[0] for x in records[1:]].count("MATERIAL_SOURCE")==1
    assert {x[15] for x in records[1:] if x[0]=="TREASURY_EVENT"}==source_ids
    assert records[1][7:15]==["2500","2751","-251","2500","2500","0","0","0"]
    for forbidden in (str(d["client_a"]),str(d["vehicle"]),
                      str(d["manager"]),"Синтетическая проверка"):
        assert forbidden not in out.text
    assert http.get(url.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(url.replace(str(org),str(uuid4())),params=query).status_code==403
    assert http.get(url,params={"fromDay":"2026-02-31",
                                "toDay":query["toDay"]}).status_code==422
    assert http.get(url,params={"fromDay":"2025-01-01",
                                "toDay":query["toDay"]}).status_code==422
    monkeypatch.setattr(module,"MAX_LEDGER_EVENTS",4)
    assert http.get(url,params=query).status_code==409
    assert http.get(url+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_LEDGER_EVENTS",500)
    monkeypatch.setattr(module,"MAX_CSV_BYTES",100)
    assert http.get(url,params=query).status_code==200
    assert http.get(url+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_CSV_BYTES",1024*1024)
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant
            SET revoked_at=now() WHERE organization_id=%s
            AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""",(org,d["intake"]))
    assert http.get(url,params=query).status_code==403
    assert http.get(url+"/export",params=query).status_code==403


def test_closed_order_no_treasury_is_not_assumed_paid(monkeypatch):
    d,org,loc,order,material,root,query,rate=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    result=http.get(_path(root),params=query)
    assert result.status_code==200,result.text
    row=result.json()["items"][0]
    assert row["ledgerStatus"]=="NO_TREASURY_EVENTS"
    assert row["demoNetMinor"]=="2500" and row["treasurySettledMinor"]=="0"
    assert row["treasuryDueMinor"]=="2500"
    assert row["demoMinusTreasuryMinor"]=="2500"
    assert row["treasuryEvents"]==[]
    assert result.json()["summary"]["ledgerLinkedCount"]==0


def test_partial_advance_never_inflates_settled_money_or_margin(monkeypatch):
    evidence={}
    d,org,loc,order,material,root,query,rate=setup(
        monkeypatch,before_cash=_hook([("ADVANCE",900)],evidence))
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    res=http.get(_path(root),params=query)
    assert res.status_code==200,res.text
    row=res.json()["items"][0]
    assert row["ledgerStatus"]=="PARTIAL"
    assert row["unappliedAdvanceMinor"]=="900"
    assert row["treasuryCashNetMinor"]=="900"
    assert row["treasurySettledMinor"]=="0"
    assert row["treasuryDueMinor"]=="2500"
    assert row["demoMinusTreasuryMinor"]=="2500"
    assert row["syntheticDifferenceMinor"]=="-251"
    assert res.json()["summary"]["ledgerSettledCount"]==0
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
        document="FINANCE",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(_path(root),params=query).status_code==403
    assert http.get(_path(root)+"/export",params=query).status_code==403


def test_missing_rate_remains_indeterminate_and_no_false_profit(monkeypatch):
    d,org,loc,order,material,root,query,rate=setup(monkeypatch,rated=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    res=http.get(_path(root),params=query)
    assert res.status_code==200,res.text
    row=res.json()["items"][0]
    assert row["costStatus"]=="INCOMPLETE"
    assert row["directCostMinor"] is None
    assert row["syntheticDifferenceMinor"] is None
    assert res.json()["marginAvailable"] is False


def test_corrupt_extra_treasury_source_does_not_leak_partial_result(monkeypatch):
    evidence={}
    d,org,loc,order,material,root,query,rate=setup(
        monkeypatch,before_cash=_hook([("ADVANCE",900)],evidence))
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    original=module.MAX_LEDGER_EVENTS
    try:
        module.MAX_LEDGER_EVENTS=0
        for path in (_path(root),_path(root)+"/export"):
            res=http.get(path,params=query)
            assert res.status_code==409
            assert res.headers["content-type"].startswith("application/json")
            assert "text/csv" not in res.headers["content-type"]
    finally:
        module.MAX_LEDGER_EVENTS=original



@pytest.mark.parametrize("mode", ["fifo", "serial"])
def test_fifo_and_serial_primary_receipts_remain_traceable_in_report_and_csv(monkeypatch,mode):
    """An audited valued FIFO/serial lot has no single group.source.

    Every allocation leg must survive the projection instead of being
    mislabeled as 'not proven' by the UI and disappearing from the CSV.
    """
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch,mode=mode)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    path=_path(root)
    report=http.get(path,params=query)
    assert report.status_code==200,report.text
    body=report.json()
    row=body["items"][0]
    assert row["costStatus"]=="COMPLETE"
    group=row["costEvidence"]["lots"][0]
    assert group["status"] == ("FIFO_TRACED" if mode=="fifo" else "SERIAL_TRACED")
    assert group["receiptId"] is None and group["valuationId"] is None
    parts=group["fifoSlices"]
    assert parts and all(part["valuationId"] and part["receiptId"]
                         and part["acceptanceId"] and part["stockEventId"]
                         and part["originalIssueId"] and part["lotMovementId"]
                         for part in parts)
    assert sum(part["quantity"]*part["unitNetMinor"]*
               (1 if part["action"]=="ISSUE" else -1) for part in parts)==(
        5750 if mode=="fifo" else int(row["directCostMinor"])-1501
    )
    if mode=="serial":
        assert all(part["serialId"] and part["serialApprovalId"]
                   for part in parts)
        # A local serial source needs no interbranch sourceLocationId.
        # That UUID is present only if material travelled between branches.
        assert all(part["sourceLocationId"] is None or
                   UUID(part["sourceLocationId"]) for part in parts)
    out=http.get(path+"/export",params=query)
    assert out.status_code==200,out.text
    csv_rows=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert all(len(record)==36 for record in csv_rows)
    slices=[record for record in csv_rows[1:] if record[0]=="FIFO_SLICE"]
    assert len(slices)==len(parts)
    assert {record[23] for record in slices}=={part["valuationId"] for part in parts}
    assert {record[25] for record in slices}=={part["receiptId"] for part in parts}
    assert {record[26] for record in slices}=={part["acceptanceId"] for part in parts}
    assert {record[27] for record in slices}=={part["lotMovementId"] for part in parts}
    assert {record[28] for record in slices}=={part["originalIssueId"] for part in parts}
    assert {record[32] for record in slices}=={part["stockEventId"] for part in parts}
    if mode=="serial":
        assert {record[29] for record in slices}=={part["serialId"] for part in parts}
        assert {record[30] for record in slices}=={part["serialApprovalId"] for part in parts}

