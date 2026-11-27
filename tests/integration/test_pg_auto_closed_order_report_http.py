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


def test_composite_branch_reports_do_not_bypass_source_document_denials(monkeypatch):
    """One actual closed order, one ADMIN session, all reporting views/exports."""
    from test_pg_auto_demo_finance_report_http import url as finance_path
    from test_pg_auto_mechanic_productivity_http import report_path
    d,org,loc,order=_close(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        stamp,zone=db.execute("""
          SELECT e.created_at,l.timezone_name FROM auto_order_demo_document_event e
          JOIN auto_work_order w ON w.id=e.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'
        """,(order,)).fetchone()
    day=stamp.astimezone(ZoneInfo(zone)).date().isoformat()
    query={"fromDay":day,"toDay":day}
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-orders"
    priced=(base,base+"/export",finance_path(org,loc))
    labor=report_path(org,loc)
    unpriced=(labor,labor+"/export",labor+"/shift-comparison",labor+"/shift-comparison/export")
    token(http,d,d["intake"])
    originals={}
    for path in priced+unpriced:
        response=http.get(path,params=query)
        assert response.status_code==200,response.text
        originals[path]=response.content
    assert http.get(base,params=query).json()["summary"]["approvedTotalMinor"]==2500
    assert http.get(labor,params=query).json()["summary"]["actualMinutes"]==45
    def policy(document,cap,allowed,cutoff=None):
        owner=token(http,d,d["other"])
        result=set_rule(http,policy_url(org),owner,role="MANAGER",loc=loc,
                        user=d["intake"],document=document,cap=cap,
                        allowed=allowed,not_before=cutoff)
        assert result.status_code==201,result.text
        token(http,d,d["intake"])
    def deny(paths):
        for path in paths:
            response=http.get(path,params=query)
            assert response.status_code==403,(path,response.text)
            assert set(response.json())=={"code","requestId"}
            assert "no-store" in response.headers["cache-control"]
            assert "content-disposition" not in response.headers
    def restore(paths):
        for path in paths:
            response=http.get(path,params=query)
            assert response.status_code==200,(path,response.text)
            assert response.content==originals[path],path
    for document in ("INTAKE_ACT","ESTIMATE","SUPPLIER_ORDER","HANDOVER","FINANCE"):
        policy(document,"READ",False)
        deny(priced)
        if document in ("INTAKE_ACT","ESTIMATE","HANDOVER"):
            deny(unpriced)
        else:
            restore(unpriced)
        policy(document,"READ",True)
        restore(priced+unpriced)
        policy(document,"READ",True,"2099-01-01T00:00:00Z")
        deny(priced)
        if document in ("INTAKE_ACT","ESTIMATE","HANDOVER"):
            empty=http.get(labor,params=query)
            assert empty.status_code==200,empty.text
            assert empty.json()["items"]==[]
            assert empty.json()["summary"]["actualMinutes"]==0
            deny(unpriced[2:])
        else:
            restore(unpriced)
        policy(document,"READ",True)
        restore(priced+unpriced)
    for document in ("ESTIMATE","HANDOVER","FINANCE"):
        policy(document,"PRICE_SALES",False)
        deny(priced)
        restore(unpriced)
        policy(document,"PRICE_SALES",True)
        restore(priced+unpriced)
    # No purchase price is returned by these reports.
    policy("SUPPLIER_ORDER","PRICE_PURCHASE",False)
    restore(priced+unpriced)
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_order_demo_document_event WHERE work_order_id=%s",(order,)).fetchone()==(3,)
        assert db.execute("SELECT count(*) FROM auto_order_demo_cash_event WHERE work_order_id=%s",(order,)).fetchone()==(1,)



def test_closed_order_period_comparison_and_reauthorized_export(monkeypatch):
    from datetime import timedelta
    d,org,loc,order=_close(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/reports/closed-orders/comparison"
    with psycopg.connect(DSN) as db:
        stamp,zone=db.execute("""SELECT e.created_at,l.timezone_name
          FROM auto_order_demo_document_event e JOIN auto_work_order w ON w.id=e.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE e.work_order_id=%s AND e.action='CLOSED_TECHNICAL'""",(order,)).fetchone()
    day=stamp.astimezone(ZoneInfo(zone)).date()
    query={"fromDay":day.isoformat(),"toDay":day.isoformat()}
    assert http.get(path,params=query).status_code==403
    token(http,d,d["client_a"])
    assert http.get(path,params=query).status_code==403
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    value=result.json()
    assert value["actualVisitsAvailable"] is False and value["retentionAvailable"] is False
    assert value["current"]["orderCount"]==value["current"]["uniqueCustomers"]==1
    assert value["current"]["singleOrderCustomers"]==1 and value["current"]["multipleOrderCustomers"]==0
    assert value["current"]["approvedTotalMinor"]==2500
    assert value["previous"]["fromDay"]==(day-timedelta(days=1)).isoformat()
    assert value["previous"]["orderCount"]==0 and value["previous"]["multipleOrderBasisPoints"] is None
    assert value["current"]["days"]==[{"day":day.isoformat(),"closedOrders":1}]
    assert value["delta"]["approvedTotalMinor"]==2500
    assert all(str(d[k]) not in result.text for k in ("ca","client_a","vehicle"))
    assert str(order) not in result.text
    report=http.get(path+"/export",params=query)
    assert report.status_code==200 and report.headers["cache-control"]=="no-store"
    rows=list(csv.reader(StringIO(report.text.lstrip("\ufeff")),delimiter=";"))
    assert [row[0] for row in rows[1:]]==["CURRENT","CURRENT_DAY","PREVIOUS","PREVIOUS_DAY","DELTA"]
    assert all(len(row)==len(rows[0]) for row in rows)
    assert rows[1][4]=="1" and rows[-1][4]=="1"
    assert http.get(path,params={"fromDay":day.isoformat(),"toDay":(day+timedelta(days=31)).isoformat()}).status_code==422
    assert http.get(path.replace(str(loc),str(uuid4())),params=query).status_code==403
    monkeypatch.setattr(module,"MAX_ORDERS",0)
    assert http.get(path,params=query).status_code==409
    assert http.get(path+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_ORDERS",200)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    empty=http.get(path,params=query).json()
    assert empty["current"]["orderCount"]==empty["current"]["uniqueCustomers"]==0
    assert empty["current"]["multipleOrderBasisPoints"] is None


def test_comparison_cohort_deduplicates_without_disclosing_keys():
    from collections import Counter
    assert module._cohort(Counter({"one":3,"two":1,"three":2}))=={
        "uniqueCustomers":3,"singleOrderCustomers":1,"multipleOrderCustomers":2,
        "additionalClosedOrders":3,"multipleOrderBasisPoints":6666}
    assert module._cohort(Counter())["multipleOrderBasisPoints"] is None
