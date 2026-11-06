"""PG17/HTTPS: inspected order → immutable mechanic finding → bound estimate → client decision."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
import json

import psycopg
import pytest

from roboport_api.auto_work_order_diagnostics import record_diagnostic,DiagnosticConflict
from roboport_api.auto_workshop_qc import submit_work_order_inspection
from test_pg_auto_work_order_estimate_http import (
    scenario,api,token,path as estimate_path,publish_payload,
)
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def diagnostic_url(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/diagnostics"


def setup(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    client=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
    return d,org,loc,order,service,foreign,client


def inspect(db,d,org,loc,order):
    submit_work_order_inspection(
        db,d["tokens"][d["intake"]],org,loc,order,
        odometer_km=41700,fuel_percent=65,tyres_ok=True,lights_ok=True,
        fluid_leak=False,exterior_code="CLEAR",
    )


def finding(service,key=None,code="FAULT_CONFIRMED",severity="SOON"):
    return {"requestKey":str(key or uuid4()),"serviceId":str(service),
            "findingCode":code,"severityCode":severity}


def test_diagnostic_full_inspection_quote_consent_acl_replay_and_sql_audit(monkeypatch):
    d,org,loc,order,service,foreign,http=setup(monkeypatch)
    url=diagnostic_url(org,loc,order)
    quote=estimate_path(org,loc,order)
    assert http.get(url).status_code==403
    mech=token(http,d,d["manager"])
    first=http.get(url)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    assert first.json()["inspectionRecorded"] is False
    assert first.json()["services"][0]["serviceId"]==str(service)
    payload=finding(service)
    assert http.post(url,headers=mech,json=payload).status_code==409
    assert http.post(url,json=payload).status_code==403
    assert http.post(url,headers=mech,json=finding(foreign)).status_code==409
    assert http.post(url,headers=mech,json=finding(service,code="NO_FAULT",
                severity="URGENT")).status_code==422
    assert http.post(url,headers=mech,json={
        **payload,"freeText":"Private report",
    }).status_code==422
    with psycopg.connect(DSN) as db:
        inspect(db,d,org,loc,order)
    recorded=http.post(url,headers=mech,json=payload)
    assert recorded.status_code==201,recorded.text
    item=recorded.json()["items"][0]
    assert item["serviceId"]==str(service)
    assert item["findingCode"]=="FAULT_CONFIRMED"
    assert item["severityCode"]=="SOON"
    assert recorded.json()["releaseAllowed"] is False
    exact=http.post(url,headers=mech,json=payload)
    assert exact.status_code==200 and exact.json()["replayed"] is True
    assert exact.json()["items"]==recorded.json()["items"]
    assert http.post(url,headers=mech,json={
        **payload,"severityCode":"URGENT",
    }).status_code==409
    assert http.post(url,headers=mech,json=finding(service)).status_code==409
    # The role is checked before reading; a CLIENT cannot probe technician findings.
    token(http,d,d["client_a"])
    assert http.get(url).status_code==403
    assert http.post(url,headers=mech,json=payload).status_code==403
    manager=token(http,d,d["intake"])
    assert http.get(url).status_code==200
    # SQL cannot suppress findings from a newly inserted quote snapshot.
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_estimate_revision
                  (id,work_order_id,organization_id,location_id,version,actor_id,
                   request_key,fingerprint,total_minor,service_lines)
                  VALUES(%s,%s,%s,%s,1,%s,%s,%s,100,%s::jsonb)""",
                  (uuid4(),order,org,loc,d["intake"],uuid4(),"a"*64,
                   json.dumps([{"serviceId":str(service),"amountMinor":100}])))
    quoted=http.post(quote,headers=manager,json=publish_payload(service))
    assert quoted.status_code==201,quoted.text
    current=quoted.json()["items"][0]
    assert current["diagnostics"]==[{
        "serviceId":str(service),"findingCode":"FAULT_CONFIRMED","severityCode":"SOON",
    }]
    assert len(current["digest"])==64
    with psycopg.connect(DSN) as db:
        row=db.execute("""SELECT diagnostics_snapshot FROM auto_work_order_estimate_revision
                          WHERE id=%s""",(UUID(current["id"]),)).fetchone()[0]
        assert row==current["diagnostics"]
        assert db.execute("SELECT count(*) FROM auto_work_order_diagnostic WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_diagnostic SET severity_code='URGENT'
                              WHERE work_order_id=%s""",(order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_diagnostic WHERE work_order_id=%s",
                           (order,))
    mechanic=token(http,d,d["manager"])
    assert http.get(url).json()["estimatePublished"] is True
    assert http.post(url,headers=mechanic,json=finding(foreign)).status_code==409
    # The exact receipt remains replayable after quote freeze.
    assert http.post(url,headers=mechanic,json=payload).json()["replayed"] is True
    customer=token(http,d,d["client_a"])
    visible=http.get(quote)
    assert visible.status_code==200,visible.text
    assert visible.json()["items"][0]["diagnostics"]==current["diagnostics"]
    approved=http.post(quote+"/decision",headers=customer,json={
        "revisionId":current["id"],"requestKey":str(uuid4()),
        "decision":"APPROVED"})
    assert approved.status_code==201,approved.text
    assert approved.json()["items"][0]["digest"]==current["digest"]
    token(http,d,d["client_b"])
    assert http.get(quote).status_code==403
    assert http.get(url.replace(str(loc),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    token(http,d,d["manager"])
    assert http.get(url).status_code==403
    assert http.post(url,headers=mech,json=payload).status_code==403


def test_concurrent_mechanics_cannot_duplicate_finding_or_change_quote(monkeypatch):
    d,org,loc,order,service,_,http=setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        inspect(db,d,org,loc,order)
    barrier=Barrier(2)
    def write(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                record_diagnostic(
                    db,d["tokens"][d["manager"]],org,loc,order,
                    request_key=uuid4(),service_id=service,
                    finding_code="MAINTENANCE_DUE",severity_code="ROUTINE")
            return "RECORDED"
        except DiagnosticConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write,(0,1)))==["CONFLICT","RECORDED"]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_diagnostic WHERE work_order_id=%s",
            (order,)).fetchone()==(1,)
    quote=estimate_path(org,loc,order)
    manager=token(http,d,d["intake"])
    published=http.post(quote,headers=manager,json=publish_payload(service))
    assert published.status_code==201,published.text
    assert published.json()["items"][0]["diagnostics"][0]["findingCode"]=="MAINTENANCE_DUE"
