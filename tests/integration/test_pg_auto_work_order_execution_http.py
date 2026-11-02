"""End-to-end PG17/HTTP: consent -> execution -> lot issue -> handoff -> QC."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_work_order_estimate_http import scenario as estimate_scenario
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import act as lot_action
from test_pg_auto_work_order_stock import act as work_stock
from roboport_api.auto_workshop_qc import submit_work_order_inspection
from roboport_api.auto_work_order_estimate import publish_auto_estimate,decide_auto_estimate
from roboport_api.auto_work_order_execution import (
    start_execution,labor_execution,finish_execution,ExecutionConflict,
)
from roboport_api.auto_workshop_qc import record_work_order_qc,InspectionConflict,InspectionDenied


def fixture(monkeypatch,with_material=True):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,service,foreign,_=estimate_scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["other"],org,loc,"MECHANIC")
        grant(db,d["intake"],org,loc,"WAREHOUSE")
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=6000,fuel_percent=50,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        rev=publish_auto_estimate(db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),expected_version=0,
            prices=[{"serviceId":service,"amountMinor":2500}])["items"][-1]["id"]
        decide_auto_estimate(db,d["tokens"][d["client_a"]],org,loc,order,
            revision_id=__import__("uuid").UUID(rev),request_key=uuid4(),
            decision="APPROVED")
        if with_material:
            item=sku(db,org,loc)
            lot=add_lot(db,org,loc,item,add_bin(db,org,loc))
            record(db,item,"RECEIPT",2)
            lot_action(db,d,org,loc,lot,"ALLOCATE",2)
            work_stock(db,d,org,loc,order,item,"RESERVE",2)
        else:
            item=lot=None
    return d,org,loc,order,service,item,lot


def url(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/execution"


def session(client,d,user):
    client.cookies.set("rp_session",d["tokens"][user])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][user]}


def post(client,path,headers=None,key=None,**kw):
    return client.post(path,headers=headers,json={"requestKey":str(key or uuid4()),**kw})


def test_full_approved_execution_stock_handoff_and_independent_qc(monkeypatch):
    d,org,loc,order,service,item,lot=fixture(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=url(org,loc,order)
    assert http.get(base).status_code==403
    manager=session(http,d,d["intake"])
    assert http.get(base).json()["status"]=="APPROVED"
    assert post(http,base+"/start").status_code==403
    key=uuid4()
    begun=post(http,base+"/start",manager,key)
    assert begun.status_code==201,begun.text
    assert begun.json()["status"]=="IN_PROGRESS"
    assert post(http,base+"/start",manager,key).status_code==200
    assert post(http,base+"/finish",manager).status_code==403
    mech=session(http,d,d["manager"])
    assert post(http,base+"/finish",mech).status_code==409
    assert post(http,base+"/labor",mech,serviceId=str(uuid4()),actualMinutes=20).status_code==409
    labor_key=uuid4()
    performed=post(http,base+"/labor",mech,labor_key,
                   serviceId=str(service),actualMinutes=32)
    assert performed.status_code==201,performed.text
    assert post(http,base+"/labor",mech,labor_key,
                serviceId=str(service),actualMinutes=32).status_code==200
    assert post(http,base+"/labor",mech,labor_key,
                serviceId=str(service),actualMinutes=25).status_code==409
    warehouse=session(http,d,d["intake"])
    issued=post(http,base+"/materials/issue",warehouse,lotId=str(lot),quantity=1)
    assert issued.status_code==201,issued.text
    issue=issued.json()["issueId"]
    assert any(not x["received"] for x in issued.json()["materials"])
    mech=session(http,d,d["manager"])
    assert post(http,base+"/finish",mech).status_code==409
    receipt_key=uuid4()
    accepted=post(http,base+"/materials/receive",mech,receipt_key,issueId=issue)
    assert accepted.status_code==201,accepted.text
    assert post(http,base+"/materials/receive",mech,receipt_key,issueId=issue).status_code==201
    assert all(x["received"] for x in accepted.json()["materials"])
    # No QC before execution completion, even with independent mechanic.
    other=session(http,d,d["other"])
    qc_path=base.removesuffix("/execution")+"/qc"
    assert post(http,qc_path,other,result="PASS",findingCode="NONE").status_code==409
    mech=session(http,d,d["manager"])
    finish_key=uuid4()
    finish=post(http,base+"/finish",mech,finish_key)
    assert finish.status_code==201,finish.text
    assert finish.json()["status"]=="READY_FOR_QC"
    assert finish.json()["releaseAllowed"] is False
    assert post(http,base+"/finish",mech,finish_key).status_code==200
    assert post(http,base+"/finish",mech).status_code==409
    warehouse=session(http,d,d["intake"])
    assert post(http,base+"/materials/issue",warehouse,
                lotId=str(lot),quantity=1).status_code==409
    # Mechanic that performed labor cannot independently QC own order.
    assert post(http,qc_path,mech,result="PASS",findingCode="NONE").status_code==403
    other=session(http,d,d["other"])
    qc=post(http,qc_path,other,result="PASS",findingCode="NONE")
    assert qc.status_code==201,qc.text
    completed=http.get(base)
    assert completed.status_code==200
    assert completed.json()["status"]=="QC_ACCEPTED"
    assert completed.json()["releaseAllowed"] is False
    with psycopg.connect(DSN) as db:
        assert balance(db,item)==(1,1)
        assert db.execute("""SELECT count(*) FROM auto_work_order_execution_finish f
            JOIN auto_work_order_execution e ON e.id=f.execution_id WHERE e.work_order_id=%s""",
            (order,)).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_labor_entry WHERE service_id=%s",(service,))


def test_declined_stale_and_revoked_proof_block_execution(monkeypatch):
    d,org,loc,order,service,_,_=fixture(monkeypatch,with_material=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=url(org,loc,order)
    manager=session(http,d,d["intake"])
    assert http.get(base.replace(str(loc),str(uuid4()))).status_code==403
    assert http.get(base.replace(str(order),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(base).status_code==403
    assert post(http,base+"/start",manager).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_execution WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==0


def test_concurrent_start_serializes_and_only_one_execution(monkeypatch):
    d,org,loc,order,service,_,_=fixture(monkeypatch,with_material=False)
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
            return "STARTED"
        except ExecutionConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==["CONFLICT","STARTED"]
