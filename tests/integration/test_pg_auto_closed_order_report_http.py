"""Real PG17 technical closing -> reconciled primary source report (#448)."""
from datetime import datetime
from io import StringIO
from zoneinfo import ZoneInfo
import csv
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api import auto_closed_order_report as module
from roboport_api.main import app
from test_pg_auto_mechanic_productivity_http import _close
from test_pg_auto_document_lifecycle_http import ready
from test_pg_baseline import DSN
from roboport_api.auto_document_lifecycle import record_document_event
from test_pg_auto_document_access_http import admin, set_rule, url as policy_url
from test_pg_auto_work_order_estimate_http import token


def test_closed_order_reconciliation_and_scope(monkeypatch):
    d,org,loc,order=_close(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-orders"
    with psycopg.connect(DSN) as db:
        stamp,zone=db.execute("""
          SELECT e.created_at,l.timezone_name FROM auto_order_demo_document_event e
          JOIN auto_work_order w ON w.id=e.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'
        """,(order,)).fetchone()
        cash=db.execute("SELECT id FROM auto_order_demo_cash_event WHERE work_order_id=%s",
                        (order,)).fetchone()[0]
    day=stamp.astimezone(ZoneInfo(zone)).date().isoformat()
    params={"fromDay":day,"toDay":day}
    assert http.get(path,params=params).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert http.get(path,params=params).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    result=http.get(path,params=params)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["summary"]=={"orderCount":1,"cashEventCount":1,
          "approvedBaseMinor":2500,"approvedExtraMinor":0,"approvedTotalMinor":2500,
          "simulatedPaidMinor":2500,"simulatedRefundedMinor":0}
    assert data["marginAvailable"] is False and data["payrollAvailable"] is False
    assert data["realMoney"] is False and data["releaseAllowed"] is False
    row=data["items"][0]
    assert row["workOrderId"]==str(order)
    assert row["cashEvents"]==[{"id":str(cash),"kind":"SIM_PAYMENT",
             "amountMinor":2500,"revisionId":row["estimateRevisionId"]}]
    assert str(d["client_a"]) not in result.text and str(d["vehicle"]) not in result.text
    export=http.get(path+"/export",params=params)
    assert export.status_code==200
    assert export.headers["x-content-type-options"]=="nosniff"
    lines=list(csv.reader(StringIO(export.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==3 and lines[1][10]=="2500"
    assert lines[2][13:]==[str(cash),"SIM_PAYMENT","2500"]
    for field in ("postedEventId","closedEventId","ackEventId","previewId"):
        assert row[field]
    assert http.get(path.replace(str(loc),str(uuid4())),params=params).status_code==403
    assert http.get(path,params={"fromDay":"2026-02-31","toDay":day}).status_code==422
    monkeypatch.setattr(module,"MAX_CASH_EVENTS",0)
    assert http.get(path,params=params).status_code==409
    assert http.get(path+"/export",params=params).status_code==409
    monkeypatch.setattr(module,"MAX_CASH_EVENTS",500)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(path,params=params).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""",(org,d["intake"]))
    assert http.get(path,params=params).status_code==403
    assert http.get(path+"/export",params=params).status_code==403


def test_paid_but_unclosed_order_is_not_in_report(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    with psycopg.connect(DSN) as db:
        zone=db.execute("SELECT timezone_name FROM auto_service_location WHERE id=%s",
                        (loc,)).fetchone()[0]
    day=datetime.now(ZoneInfo(zone)).date().isoformat()
    result=http.get(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-orders",
                    params={"fromDay":day,"toDay":day})
    assert result.status_code==200,result.text
    assert result.json()["summary"]["orderCount"]==0
    assert result.json()["items"]==[]


def test_refund_repayment_and_policy_cutoff_are_reconciled_against_real_pg(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch,with_refund=True)
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
    query={"fromDay":day,"toDay":day}
    http=TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-orders"
    token(http,d,d["intake"])
    response=http.get(path,params=query)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["summary"]=={"orderCount":1,"cashEventCount":3,
          "approvedBaseMinor":2500,"approvedExtraMinor":0,"approvedTotalMinor":2500,
          "simulatedPaidMinor":3000,"simulatedRefundedMinor":500}
    assert [x["kind"] for x in data["items"][0]["cashEvents"]]==[
        "SIM_PAYMENT","SIM_REFUND","SIM_PAYMENT"]
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES").status_code==201
    token(http,d,d["intake"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES",allowed=True).status_code==201
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="HANDOVER",cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z").status_code==201
    token(http,d,d["intake"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403
