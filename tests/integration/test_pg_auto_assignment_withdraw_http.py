"""PG17 + HTTPS: полный отзыв незавершённого предложения механика.

Проверка аудита, BOLA, отозванной смены, переназначения и гонки
ACCEPTED ↔ WITHDRAWN. Только synthetic-данные.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_work_order_assignment import (
    respond_assignment,withdraw_assignment,AssignmentConflict,
)
from test_pg_auto_work_order_assignment_http import auth
from test_pg_auto_work_order_estimate_http import scenario
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def ready(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,service,foreign,_=scenario()
    starts=datetime.now(timezone.utc)+timedelta(hours=3)
    ends=starts+timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["other"],org,loc,"MECHANIC")
        for person in (d["manager"],d["other"]):
            db.execute("""INSERT INTO auto_staff_shift
               (id,organization_id,location_id,user_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",
               (uuid4(),org,loc,person,
                starts-timedelta(minutes=5),ends+timedelta(minutes=5)))
    http=TestClient(app,base_url="https://127.0.0.1")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    return d,org,loc,order,foreign,http,root,starts,ends


def offer(http,root,order,headers,mechanic,starts,ends):
    return http.post(root+f"/orders/{order}/assignments",headers=headers,json={
        "mechanicId":str(mechanic),"startsAt":starts.isoformat(),
        "endsAt":ends.isoformat(),"requestKey":str(uuid4()),
    })


def test_manager_withdraw_reassign_immutable_audit_bola_and_idempotency(monkeypatch):
    d,org,loc,order,foreign,http,root,starts,ends=ready(monkeypatch)
    manager=auth(http,d,d["intake"])
    first=offer(http,root,order,manager,d["manager"],starts,ends)
    assert first.status_code==201,first.text
    first_id=first.json()["assignmentId"]
    queue=http.get(root+"/assignments")
    assert queue.status_code==200,queue.text
    assert len(queue.json()["withdrawable"])==1
    assert queue.json()["withdrawable"][0]["assignmentId"]==first_id
    assert len(queue.json()["items"])==1
    endpoint=root+f"/assignments/{first_id}/withdraw"
    assert http.post(endpoint,json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint,headers=manager,json={"requestKey":"bad"}).status_code==422
    assert http.post(root+"/assignments/"+str(uuid4())+"/withdraw",
                     headers=manager,json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint.replace(str(loc),str(uuid4())),
                     headers=manager,json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint.replace(str(org),str(uuid4())),
                     headers=manager,json={"requestKey":str(uuid4())}).status_code==403
    key=str(uuid4())
    done=http.post(endpoint,headers=manager,json={"requestKey":key})
    assert done.status_code==201,done.text
    assert done.json()=={"ok":True,"replayed":False,"status":"WITHDRAWN"}
    replay=http.post(endpoint,headers=manager,json={"requestKey":key})
    assert replay.status_code==200
    assert replay.json()=={"ok":True,"replayed":True,"status":"WITHDRAWN"}
    again=http.post(endpoint,headers=manager,json={"requestKey":str(uuid4())})
    assert again.status_code==409 and again.json()["code"]=="ASSIGNMENT_NOT_OFFERED"
    assert http.get(root+"/assignments").json()["withdrawable"]==[]

    mechanic=auth(http,d,d["manager"])
    old_accept=http.post(root+f"/assignments/{first_id}/accept",headers=mechanic,
        json={"requestKey":str(uuid4())})
    assert old_accept.status_code==409,old_accept.text

    manager=auth(http,d,d["intake"])
    second=offer(http,root,order,manager,d["other"],starts,ends)
    assert second.status_code==201,second.text
    other_id=second.json()["assignmentId"]
    assert other_id!=first_id
    assert len(http.get(root+"/assignments").json()["withdrawable"])==1
    # Принятая задача не может быть отозвана менеджером: это уже работы.
    another=auth(http,d,d["other"])
    accepted=http.post(root+f"/assignments/{other_id}/accept",headers=another,
        json={"requestKey":str(uuid4())})
    assert accepted.status_code==201,accepted.text
    manager=auth(http,d,d["intake"])
    fail=http.post(root+f"/assignments/{other_id}/withdraw",headers=manager,
        json={"requestKey":str(uuid4())})
    assert fail.status_code==409 and fail.json()["code"]=="ASSIGNMENT_NOT_OFFERED"
    assert http.get(root+"/assignments").json()["withdrawable"]==[]

    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT status,status_actor_id,accepted_at
          FROM auto_work_order_assignment WHERE id=%s""",(UUID(first_id),)
        ).fetchone()==("WITHDRAWN",d["intake"],None)
        assert db.execute("""SELECT action,actor_id FROM auto_work_order_assignment_event
          WHERE assignment_id=%s ORDER BY created_at,id""",(UUID(first_id),)
        ).fetchall()==[("OFFERED",d["intake"]),("WITHDRAWN",d["intake"])]
        assert db.execute("""SELECT count(*) FROM auto_work_order_assignment_event
          WHERE assignment_id=%s AND action='WITHDRAWN'""",
          (UUID(first_id),)).fetchone()==(1,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_assignment SET status='WITHDRAWN',
                   status_actor_id=%s,accepted_at=NULL WHERE id=%s""",
                   (d["intake"],UUID(other_id)))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_work_order_assignment_event
                   WHERE assignment_id=%s""",(UUID(first_id),))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_assignment SET
                   status='OFFERED',status_actor_id=NULL WHERE id=%s""",
                   (UUID(first_id),))


def test_withdraw_hidden_shift_revocation_manager_only_and_revoked_retry(monkeypatch):
    d,org,loc,order,foreign,http,root,starts,ends=ready(monkeypatch)
    manager=auth(http,d,d["intake"])
    created=offer(http,root,order,manager,d["manager"],starts,ends)
    assert created.status_code==201,created.text
    ident=created.json()["assignmentId"]
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_staff_shift SET canceled_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s""",
          (org,loc,d["manager"]))
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s
            AND role_code='MECHANIC' AND revoked_at IS NULL""",
          (org,loc,d["manager"]))
        db.execute("""UPDATE auto_vehicle_customer_link SET revoked_at=now()
          WHERE id=%s""",(d["link"],))
    original=http.get(root+"/assignments")
    assert original.status_code==200,original.text
    assert original.json()["items"]==[]
    assert len(original.json()["withdrawable"])==1
    assert original.json()["withdrawable"][0]["assignmentId"]==ident
    client=auth(http,d,d["client_a"])
    assert http.get(root+"/assignments").status_code==403
    assert http.post(root+f"/assignments/{ident}/withdraw",headers=client,
                     json={"requestKey":str(uuid4())}).status_code==403
    other=auth(http,d,d["other"])
    mechanic_queue=http.get(root+"/assignments")
    assert mechanic_queue.status_code==200
    assert mechanic_queue.json()["withdrawable"]==[]
    assert http.post(root+f"/assignments/{ident}/withdraw",headers=other,
                     json={"requestKey":str(uuid4())}).status_code==403
    manager=auth(http,d,d["intake"])
    key=str(uuid4())
    done=http.post(root+f"/assignments/{ident}/withdraw",headers=manager,
                   json={"requestKey":key})
    assert done.status_code==201,done.text
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""",(org,d["intake"]))
    assert http.post(root+f"/assignments/{ident}/withdraw",headers=manager,
                     json={"requestKey":key}).status_code==403
    assert http.get(root+"/assignments").status_code==403


def test_competing_accept_and_manager_withdraw_exactly_one_wins(monkeypatch):
    d,org,loc,order,foreign,http,root,starts,ends=ready(monkeypatch)
    manager=auth(http,d,d["intake"])
    created=offer(http,root,order,manager,d["manager"],starts,ends)
    assert created.status_code==201,created.text
    ident=UUID(created.json()["assignmentId"])
    barrier=Barrier(2)
    def worker(mode):
        barrier.wait(timeout=15)
        with psycopg.connect(DSN) as db:
            try:
                if mode=="accept":
                    result=respond_assignment(
                        db,d["tokens"][d["manager"]],org,loc,ident,uuid4(),True)
                else:
                    result=withdraw_assignment(
                        db,d["tokens"][d["intake"]],org,loc,ident,uuid4())
                return result["status"]
            except AssignmentConflict:
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(worker,["accept","withdraw"]))
    assert "CONFLICT" in results
    assert any(x in ("ACCEPTED","WITHDRAWN") for x in results)
    with psycopg.connect(DSN) as db:
        status=db.execute("SELECT status FROM auto_work_order_assignment WHERE id=%s",
                          (ident,)).fetchone()[0]
        assert status in ("ACCEPTED","WITHDRAWN")
        events=db.execute("""SELECT action FROM auto_work_order_assignment_event
          WHERE assignment_id=%s""",(ident,)).fetchall()
        assert len(events)==2
        assert set(action for (action,) in events)=={"OFFERED",status}
