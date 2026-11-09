"""PG17/HTTP assignment offers: role, shift, idempotency and branch isolation."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_work_order_estimate_http import scenario
from test_pg_auto_stock_service import grant
from roboport_api.auto_workshop_qc import submit_work_order_inspection


def auth(client,d,user):
    client.cookies.set("rp_session",d["tokens"][user])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][user]}


def test_shift_aware_assignment_accept_replay_and_authority(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,service,foreign,_=scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["other"],org,loc,"MECHANIC")
        starts=datetime.now(timezone.utc)+timedelta(hours=2)
        ends=starts+timedelta(hours=2)
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["manager"],starts-timedelta(minutes=5),ends+timedelta(minutes=5)))
    client=TestClient(app,base_url="https://127.0.0.1")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    path=root+f"/orders/{order}/assignments"
    key=uuid4()
    body={"mechanicId":str(d["manager"]),"startsAt":starts.isoformat(),
          "endsAt":ends.isoformat(),"requestKey":str(key)}
    assert client.post(path,json=body).status_code==403
    manager=auth(client,d,d["intake"])
    no_shift={**body,"mechanicId":str(d["other"]),"requestKey":str(uuid4())}
    assert client.post(path,headers=manager,json=no_shift).status_code==403
    offered=client.post(path,headers=manager,json=body)
    assert offered.status_code==201,offered.text
    assignment=offered.json()["assignmentId"]
    replay=client.post(path,headers=manager,json=body)
    assert replay.status_code==200 and replay.json()["replayed"] is True
    assert client.post(path,headers=manager,json={**body,"endsAt":(ends+timedelta(minutes=5)).isoformat()}).status_code==409
    # A second active assignment to the same order is never allowed.
    other_offer={**body,"requestKey":str(uuid4())}
    assert client.post(path,headers=manager,json=other_offer).status_code==409
    auth(client,d,d["client_a"])
    queue=client.get(root+"/assignments")
    assert queue.status_code==403
    mechanic=auth(client,d,d["manager"])
    items=client.get(root+"/assignments")
    assert items.status_code==200 and len(items.json()["items"])==1
    accept_path=root+f"/assignments/{assignment}/accept"
    accept_key=str(uuid4())
    accepted=client.post(accept_path,headers=mechanic,json={"requestKey":accept_key})
    assert accepted.status_code==201,accepted.text
    assert accepted.json()["status"]=="ACCEPTED"
    assert client.post(accept_path,headers=mechanic,json={"requestKey":accept_key}).status_code==200
    assert client.post(accept_path,headers=mechanic,json={"requestKey":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=1200,fuel_percent=70,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
    # Accepted executor is never an independent QC checker, even before
    # recording a labor entry; neither the queue nor direct POST bypasses it.
    queue=client.get(root+"/inspection-qc/pending")
    assert queue.status_code==200
    assert str(order) not in [x["orderId"] for x in queue.json()["items"]]
    self_qc=client.post(root+f"/orders/{order}/qc",headers=mechanic,json={
        "requestKey":str(uuid4()),"result":"PASS","findingCode":"NONE"})
    assert self_qc.status_code==403,self_qc.text
    other=auth(client,d,d["other"])
    queue=client.get(root+"/inspection-qc/pending")
    assert queue.status_code==200
    assert str(order) in [x["orderId"] for x in queue.json()["items"]]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT status FROM auto_work_order_assignment
          WHERE id=%s""",(assignment,)).fetchone()==("ACCEPTED",)
        assert db.execute("""SELECT action FROM auto_work_order_assignment_event
          WHERE assignment_id=%s ORDER BY created_at,id""",(assignment,)).fetchall()==[
            ("OFFERED",),("ACCEPTED",)]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_assignment_event WHERE assignment_id=%s",
                           (assignment,))


def test_assignment_rejects_foreign_scope_and_revoked_mechanic(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,service,foreign,_=scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        starts=datetime.now(timezone.utc)+timedelta(hours=3);ends=starts+timedelta(hours=1)
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["manager"],starts-timedelta(minutes=1),ends+timedelta(minutes=1)))
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MECHANIC'""",
          (org,d["manager"]))
    client=TestClient(app,base_url="https://127.0.0.1")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/assignments"
    manager=auth(client,d,d["intake"])
    body={"mechanicId":str(d["manager"]),"startsAt":starts.isoformat(),
          "endsAt":ends.isoformat(),"requestKey":str(uuid4())}
    assert client.post(root,headers=manager,json=body).status_code==403
