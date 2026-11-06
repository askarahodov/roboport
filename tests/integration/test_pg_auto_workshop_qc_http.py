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
    # A QC FAIL cannot be silently changed into PASS without corrective action.
    assert client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                "result":"PASS","findingCode":"NONE"}).status_code==409
    rework=qc+"/rework"
    assert client.post(rework,headers=mechanic,json={"requestKey":str(uuid4()),
                "failEventId":str(uuid4()),"actionCode":"ADJUSTMENT"}).status_code==409
    fail_id=failed.json()["qcEvents"][-1]["id"]
    fix_key=str(uuid4())
    worker=token_as(client,d,d["intake"])
    action=client.post(rework,headers=worker,json={"requestKey":fix_key,
                "failEventId":fail_id,"actionCode":"ADJUSTMENT"})
    assert action.status_code==201,action.text
    assert len(action.json()["reworks"])==1
    repeated_action=client.post(rework,headers=worker,json={"requestKey":fix_key,
                "failEventId":fail_id,"actionCode":"ADJUSTMENT"})
    assert repeated_action.status_code==200 and repeated_action.json()["replayed"] is True
    assert client.post(rework,headers=worker,json={"requestKey":fix_key,
                "failEventId":fail_id,"actionCode":"COMPONENT_RECHECK"}).status_code==409
    assert client.post(rework,headers=worker,json={"requestKey":str(uuid4()),
                "failEventId":fail_id,"actionCode":"ADJUSTMENT"}).status_code==409
    mechanic=token_as(client,d,d["manager"])
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
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_rework WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_qc_rework WHERE work_order_id=%s",
                           (order,))
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


def test_concurrent_independent_qc_pass_allows_only_one_final_decision(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    d,org,loc,_,order,*_=scenario()
    client=client_setup(monkeypatch)
    _,inspect,qc=target(org,loc,order)
    intake=token_as(client,d,d["intake"])
    assert client.post(inspect,headers=intake,json=inspection()).status_code==201
    barrier=Barrier(2)
    def submit(_):
        own=TestClient(app,base_url="https://127.0.0.1")
        own.cookies.set("rp_session",d["tokens"][d["manager"]])
        headers={"Origin":"https://127.0.0.1",
                 "X-CSRF-Token":d["csrf"][d["manager"]]}
        barrier.wait(timeout=10)
        return own.post(qc,headers=headers,json={
            "requestKey":str(uuid4()),"result":"PASS","findingCode":"NONE"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,range(2)))
    assert sorted(result.status_code for result in results)==[201,409]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_work_order_qc_event
                             WHERE work_order_id=%s AND result='PASS'""",
                          (order,)).fetchone()[0]==1



def test_qc_manager_queue_scoped_first_fail_pass_and_pagination(monkeypatch):
    """Current eligible WorkOrders, not historical passed/cancelled or other branches."""
    d,org,loc,bay,order,_,start,_,foreign_loc,_,mechanic_grant=scenario()
    with psycopg.connect(DSN) as db:
        _,_,second=create_appointment(db,d["tokens"][d["intake"]],
            org,loc,bay,d["link"],start+timedelta(hours=2),
            start+timedelta(hours=3))
    client=client_setup(monkeypatch)
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    queue=base+"/inspection-qc/pending"
    assert client.get(queue).status_code==403
    intake=token_as(client,d,d["intake"])
    assert client.get(queue).status_code==200  # intake also holds MECHANIC
    assert client.get(queue).json()["items"]==[]
    for work_order in (order,second):
        _,inspect,_=target(org,loc,work_order)
        assert client.post(inspect,headers=intake,json=inspection()).status_code==201

    # The inspector also has the MECHANIC grant but cannot review own work.
    assert client.get(queue).json()["items"]==[]
    mechanic=token_as(client,d,d["manager"])
    first=client.get(queue,params={"limit":1})
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    assert first.json()["syntheticOnly"] is True
    assert len(first.json()["items"])==1
    cursor=first.json()["nextOrderId"]
    assert cursor==first.json()["items"][0]["orderId"]
    second_page=client.get(queue,params={"limit":1,"afterOrderId":cursor})
    assert second_page.status_code==200,second_page.text
    assert second_page.json()["nextOrderId"] is None
    ids={first.json()["items"][0]["orderId"],
         second_page.json()["items"][0]["orderId"]}
    assert ids=={str(order),str(second)}
    assert all(row["status"]=="PENDING_QC" and row["lastFindingCode"]=="NONE"
               for row in first.json()["items"]+second_page.json()["items"])
    for personal in (str(d["vehicle"]),str(d["ca"]),str(d["manager"])):
        assert personal not in first.text
    assert client.get(queue,params={"limit":0}).status_code==422
    assert client.get(queue,params={"limit":51}).status_code==422
    assert client.get(queue,params={"afterOrderId":"bad"}).status_code==422
    foreign=queue.replace(str(loc),str(foreign_loc))
    assert client.get(foreign).status_code==403

    _,_,qc=target(org,loc,order)
    failed=client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                "result":"FAIL","findingCode":"REWORK"})
    assert failed.status_code==201,failed.text
    changed=client.get(queue)
    assert changed.status_code==200,changed.text
    assert {row["orderId"] for row in changed.json()["items"]}==ids
    assert next(row for row in changed.json()["items"]
                if row["orderId"]==str(order))["status"]=="FAIL"
    assert client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                "result":"PASS","findingCode":"NONE"}).status_code==409
    fail_id=failed.json()["qcEvents"][-1]["id"]
    worker=token_as(client,d,d["intake"])
    fixed=client.post(qc+"/rework",headers=worker,json={"requestKey":str(uuid4()),
                "failEventId":fail_id,"actionCode":"DIAGNOSTIC_RECHECK"})
    assert fixed.status_code==201,fixed.text
    mechanic=token_as(client,d,d["manager"])
    passed=client.post(qc,headers=mechanic,json={"requestKey":str(uuid4()),
                "result":"PASS","findingCode":"NONE"})
    assert passed.status_code==201,passed.text
    after=client.get(queue)
    assert after.status_code==200
    assert [row["orderId"] for row in after.json()["items"]]==[str(second)]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (mechanic_grant,))
    assert client.get(queue).status_code==403


def test_qc_queue_excludes_revoked_proof_and_cancelled_appointment(monkeypatch):
    d,org,loc,bay,order,appt,_,_,_,_,_=scenario()
    client=client_setup(monkeypatch)
    _,inspect,_=target(org,loc,order)
    intake=token_as(client,d,d["intake"])
    assert client.post(inspect,headers=intake,json=inspection()).status_code==201
    token_as(client,d,d["manager"])
    queue=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/inspection-qc/pending"
    assert [row["orderId"] for row in client.get(queue).json()["items"]]==[str(order)]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(queue).json()["items"]==[]


def test_qc_rework_independent_pass_sql_guard_concurrency_and_proof_revoke(monkeypatch):
    """FAIL cannot become PASS without a distinct worker; direct SQL cannot bypass it."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import UUID
    from roboport_api.auto_workshop_qc import record_work_order_qc_rework, InspectionConflict
    d,org,loc,_,order,*_=scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["other"],org,loc,"MECHANIC")
    client=client_setup(monkeypatch)
    read,inspect,qc=target(org,loc,order)
    intake=token_as(client,d,d["intake"])
    assert client.post(inspect,headers=intake,json=inspection()).status_code==201
    checker=token_as(client,d,d["manager"])
    failed=client.post(qc,headers=checker,json={"requestKey":str(uuid4()),
                           "result":"FAIL","findingCode":"DEFECT"})
    assert failed.status_code==201,failed.text
    failed_id=UUID(failed.json()["qcEvents"][-1]["id"])
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_qc_event
                  (id,work_order_id,inspection_id,organization_id,location_id,
                   actor_id,request_key,result,finding_code)
                  SELECT %s,work_order_id,id,organization_id,location_id,
                         %s,%s,'PASS','NONE'
                  FROM auto_work_order_inspection WHERE work_order_id=%s""",
                  (uuid4(),d["manager"],uuid4(),order))
    assert client.post(qc,headers=checker,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"}).status_code==409
    # Competing workers can create only one immutable record for this FAIL.
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                return record_work_order_qc_rework(db,d["tokens"][d["manager"]],
                    org,loc,order,request_key=uuid4(),fail_event_id=failed_id,
                    action_code="COMPONENT_RECHECK")["replayed"]
        except InspectionConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,range(2)))
    assert sorted(results,key=str)==[False,"CONFLICT"]
    # The actor that attested corrective action cannot approve their own PASS.
    checker=token_as(client,d,d["manager"])
    assert client.post(qc,headers=checker,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"}).status_code==409
    independent=token_as(client,d,d["other"])
    passed=client.post(qc,headers=independent,json={"requestKey":str(uuid4()),
                 "result":"PASS","findingCode":"NONE"})
    assert passed.status_code==201,passed.text
    assert passed.json()["status"]=="QC_PASSED"
    assert len(passed.json()["reworks"])==1
    assert client.post(qc+"/rework",headers=independent,json={
        "requestKey":str(uuid4()),"failEventId":str(failed_id),
        "actionCode":"ADJUSTMENT"}).status_code==409
    # Do not permit a stale receipt to bypass later revocation.
    rework_row=passed.json()["reworks"][0]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    checker=token_as(client,d,d["manager"])
    assert client.post(qc+"/rework",headers=checker,json={
        "requestKey":str(uuid4()),"failEventId":str(failed_id),
        "actionCode":"COMPONENT_RECHECK"}).status_code==403
    assert client.get(read).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_rework WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_work_order_qc_event WHERE work_order_id=%s",
                          (order,)).fetchone()==(2,)
