"""Real PG17 HTTPS: immutable lot issue/return and receipt quote provenance, never COGS."""
from datetime import datetime, timezone
from io import StringIO
from uuid import uuid4
from zoneinfo import ZoneInfo
import csv

import psycopg
from fastapi.testclient import TestClient

from roboport_api import auto_material_cost_evidence as module
from roboport_api.main import app
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document, accept_receipt_document,
)
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from test_pg_auto_stock_lot_work_order_issue import setup, issue
from test_pg_auto_stock_lot_work_order_return import ret
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def path(org,loc):
    return (f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
            "/reports/material-cost-evidence")


def fixture(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,warehouse_grant=setup(db,d,5)
        manager_grant=grant(db,d["intake"],org,loc,"MANAGER")
        grant(db,d["manager"],org,loc,"MANAGER")
        db.execute(
            "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
            (d["vehicle"],loc),
        )
        # The historic stock setup has no accepted supplier document.
        issued=issue(db,d,org,loc,order,lot,3)
        returned=ret(db,d,org,loc,order,lot,1)
        day=db.execute(
            "SELECT (clock_timestamp() AT TIME ZONE timezone_name)::date "
            "FROM auto_service_location WHERE id=%s", (loc,)
        ).fetchone()[0].isoformat()
    return d,org,loc,order,item,lot,manager_grant,issued,returned,day


def _get(http,org,loc,day):
    return http.get(path(org,loc),params={"fromDay":day,"toDay":day})


def test_real_lot_issue_return_receipt_quote_and_multi_source_not_cogs(monkeypatch):
    d,org,loc,order,item,lot,manager_grant,issued,returned,day=fixture(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    assert _get(http,org,loc,day).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert _get(http,org,loc,day).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    first=_get(http,org,loc,day)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    data=first.json()
    assert (data["syntheticOnly"],data["realMoney"],data["marginAvailable"],
            data["releaseAllowed"],data["priceIsOnlyReceiptQuote"])==(
                True,False,False,False,True)
    assert data["summary"]=={
        "workOrderCount":1,"lotCount":1,"movementCount":2,
        "grossIssued":3,"grossReturned":1,"netPeriod":2,
        "evidenceCounts":{"NO_RECEIPT":1,"REVIEW_REQUIRED":0,"SINGLE_VALUED_RECEIPT":0},
    }
    row=data["items"][0]
    assert row["workOrderId"]==str(order) and row["lotId"]==str(lot)
    assert row["quoteEvidence"]=="NO_RECEIPT"
    assert row["movementCount"]==2
    assert set(row["movementIds"])=={str(issued),str(returned)}
    assert row["quoteUnitNetMinor"] is None
    assert str(d["client_a"]) not in first.text

    with psycopg.connect(DSN) as db:
        # Source is a newly accepted and independently valued receipt.
        # It DOES NOT prove the issued stock came from this receipt:
        # the lot already contained synthetic stock before the document.
        document=create_receipt_document(
            db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),2,"DOC-AUDIT-1")
        accept_receipt_document(
            db,d["tokens"][d["manager"]],org,loc,document,uuid4())
        valuation=record_receipt_valuation(
            db,d["tokens"][d["manager"]],org,loc,document,uuid4(),
            "RUB",700,140,"QUOTE-AUDIT-1",datetime(2026,10,28,tzinfo=timezone.utc))
    second=_get(http,org,loc,day)
    assert second.status_code==200,second.text
    assert second.json()["items"][0]["quoteEvidence"]=="SINGLE_VALUED_RECEIPT"
    assert second.json()["items"][0]["quoteUnitNetMinor"]=="700"
    assert second.json()["items"][0]["quoteValuationId"]==str(valuation)
    assert second.json()["marginAvailable"] is False

    exported=http.get(path(org,loc)+"/export",
                      params={"fromDay":day,"toDay":day})
    assert exported.status_code==200,exported.text
    assert exported.headers["x-content-type-options"]=="nosniff"
    lines=list(csv.reader(StringIO(exported.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==2
    assert lines[1][:3]==[str(order),str(lot),str(item)]
    assert lines[1][3:6]==["3","1","2"]
    assert lines[1][-1]=="QUOTE_ONLY_NOT_COGS"

    with psycopg.connect(DSN) as db:
        # Mixed incoming provenance must NEVER silently pick the first
        # document's quote as a valid issue-layer cost.
        extra=create_receipt_document(
            db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),1,"DOC-AUDIT-2")
    third=_get(http,org,loc,day)
    assert third.status_code==200,third.text
    row=third.json()["items"][0]
    assert row["receiptCount"]==2 and row["valuedReceiptCount"]==1
    assert row["quoteEvidence"]=="REVIEW_REQUIRED"
    assert row["quoteUnitNetMinor"] is None
    assert row["quoteCurrency"] is None
    assert row["quoteValuationId"] is None
    assert third.json()["summary"]["evidenceCounts"]["REVIEW_REQUIRED"]==1

    assert _get(http,org,uuid4(),day).status_code==403
    assert http.get(path(org,loc),params={
        "fromDay":"2026-02-31","toDay":day}).status_code==422
    assert http.get(path(org,loc),params={
        "fromDay":"2026-01-01","toDay":day}).status_code==422

    # All-or-nothing capacity enforced by the *real database query*.
    monkeypatch.setattr(module,"MAX_EVENTS",1)
    assert _get(http,org,loc,day).status_code==409
    assert http.get(path(org,loc)+"/export",
                    params={"fromDay":day,"toDay":day}).status_code==409
    monkeypatch.setattr(module,"MAX_EVENTS",500)

    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (manager_grant,))
    assert _get(http,org,loc,day).status_code==403
    assert http.get(path(org,loc)+"/export",
                    params={"fromDay":day,"toDay":day}).status_code==403


def test_material_audit_local_day_dst_and_csv_empty():
    berlin=ZoneInfo("Europe/Berlin")
    spring=module._period("2026-03-29","2026-03-29",berlin)
    autumn=module._period("2026-10-25","2026-10-25",berlin)
    assert (spring[1]-spring[0]).total_seconds()==23*3600
    assert (autumn[1]-autumn[0]).total_seconds()==25*3600
    assert module._csv({"items":[]}).startswith("\ufeffЗаказ-наряд UUID")
