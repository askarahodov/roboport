"""PG17 + HTTPS contract for immutable synthetic intake and independent QC."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_appointment_service import create_appointment, reschedule_appointment
from roboport_api.auto_workshop_qc import (
    InspectionDenied, read_work_order_inspection, submit_work_order_inspection
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def scenario():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        foreign,foreign_loc,_=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        intake_grant=grant(db,d["intake"],org,loc,"INTAKE")
        mechanic_grant=grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["intake"],org,loc,"MECHANIC")
    start=datetime.now(timezone.utc)+timedelta(days=4)
    with psycopg.connect(DSN) as db:
        appt,booking,order=create_appointment(db,d["tokens"][d["intake"]],
                        org,loc,bay,d["link"],start,start+timedelta(hours=1))
    return d,org,loc,bay,order,appt,start,foreign,foreign_loc,intake_grant,mechanic_grant


def client_setup(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    return TestClient(app,base_url="https://127.0.0.1")


def target(org,loc,order):
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}"
    return base+"/inspection-qc",base+"/inspection",base+"/qc"


def token_as(client,d,user):
    client.cookies.set("rp_session",d["tokens"][user])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][user]}


def inspection():
    return {"odometerKm":75000,"fuelPercent":42,"tyresOk":True,
            "lightsOk":False,"fluidLeak":False,"exteriorCode":"DAMAGE"}


def test_intake_qc_fail_rework_pass_audit_and_retries(monkeypatch):
    d,org,loc,_,order,_,_,_,_,intake_grant,mechanic_grant=scenario()
    client=client_setup(monkeypatch)
    read,inspect,qc=target(org,loc,order)
    assert client.get(read).status_code==403
    assert client.post(inspect,json=inspection()).status_code==403
    intake=token_as(client,d,d["intake"])
    initial=client.get(read)
    assert initial.status_code==200 and initial.json()["status"]=="NOT_INSPECTED"
    assert initial.json()["releaseAllowed"] is False
    assert client.post(inspect,json=inspection()).status_code==403
    inserted=client.post(inspect,headers=intake,json=inspection())
    assert inserted.status_code==201,inserted.text
    assert inserted.json()["status"]=="PENDING_QC"
    assert inserted.json()["inspection"]["odometerKm"]==75000
    repeated=client.post(inspect,headers=intake,json=inspection())
    assert repeated.status_code==200 and repeated.json()["replayed"] is True
    assert client.post(inspect,headers=intake,
                       json={**inspection(),"odometerKm":76000}).status_code==409
    key=str(uuid4())
    assert client.post(qc,headers=intake,json={"requestKey":key,
                        "result":"PASS","findingCode":"NONE"}).status_code==403
    mechanic=token_as(client,d,d["manager"])
    failed=client.post(qc,headers=mechanic,json={"requestKey":key,
                        "result":"FAIL","findingCode":"REWORK"})
    assert failed.status_code==201,failed.text
    assert failed.json()["status"]=="QC_FAILED"
    assert failed.json()["releaseAllowed"] is False
    assert client.post(qc,headers=mechanic,json={"requestKey":key,
                 "result":"FAIL","findingCode":"REWORK"}).status_code==200
    assert client.post(qc,headers=mechanic,json={"requestKey":key,
                 "result":"PASS","findingCode":"NONE"}).status_code==409
    passed=client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                        "result":"PASS","findingCode":"NONE"})
    assert passed.status_code==201,passed.text
    assert passed.json()["status"]=="QC_PASSED"
    assert passed.json()["releaseAllowed"] is False
    assert len(passed.json()["qcEvents"])==2
    assert client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                 "result":"FAIL","findingCode":"DEFECT"}).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_event WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_qc_event WHERE work_order_id=%s",(order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order_inspection SET fuel_percent=50 WHERE work_order_id=%s",(order,))
    assert client.get(read).json()["status"]=="QC_PASSED"


def test_scope_revoke_cancellation_and_separate_mechanic(monkeypatch):
    d,org,loc,bay,order,appointment,start,foreign,foreign_loc,intake_grant,mechanic_grant=scenario()
    client=client_setup(monkeypatch)
    read,inspect,qc=target(org,loc,order)
    intake=token_as(client,d,d["intake"])
    assert client.get(read.replace(str(loc),str(foreign_loc))).status_code==403
    assert client.get(read.replace(str(order),str(uuid4()))).status_code==403
    assert client.post(inspect,headers=intake,json={**inspection(),"fuelPercent":101}).status_code==422
    assert client.post(inspect,headers=intake,json=inspection()).status_code==201
    # Same employee cannot QC their own inspection, even with a MECHANIC grant.
    assert client.post(qc,headers=intake,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (intake_grant,))
    assert client.post(inspect,headers=intake,json=inspection()).status_code==403
    mechanic=token_as(client,d,d["manager"])
    assert client.get(read).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(read).status_code==403
    assert client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"}).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_event WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==0


def test_qc_allowed_after_reschedule_but_not_after_cancellation(monkeypatch):
    d,org,loc,bay,order,appointment,start,*_=scenario()
    client=client_setup(monkeypatch)
    read,inspect,qc=target(org,loc,order)
    intake=token_as(client,d,d["intake"])
    assert client.post(inspect,headers=intake,json=inspection()).status_code==201
    with psycopg.connect(DSN) as db:
        reschedule_appointment(db,d["tokens"][d["intake"]],org,appointment,bay,
                               start+timedelta(hours=2),start+timedelta(hours=3))
    mechanic=token_as(client,d,d["manager"])
    result=client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                       "result":"FAIL","findingCode":"DEFECT"})
    assert result.status_code==201,result.text
    # Business cancellation makes both reads and further QC unavailable.
    from roboport_api.auto_appointment_service import cancel_appointment
    with psycopg.connect(DSN) as db:
        cancel_appointment(db,d["tokens"][d["intake"]],org,appointment)
    assert client.get(read).status_code==403
    assert client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"}).status_code==403
