"""PG17 + HTTPS: полное безопасное освобождение принятого до начала работ.

Права MANAGER, сохранение ACCEPTED timestamp, BOLA, race со стартом
исполнения, запрет после старта, новое назначение и история.
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
    AssignmentConflict,release_accepted_assignment,respond_assignment,
)
import roboport_api.auto_work_order_assignment_http as assignment_routes
from roboport_api.auto_work_order_execution import start_execution,ExecutionConflict
from test_pg_baseline import DSN
from test_pg_auto_assignment_withdraw_http import ready,offer,auth
from test_pg_auto_work_order_execution_http import fixture as approved_fixture,url as exec_url
from test_pg_auto_stock_service import grant


def accepted_order(http,root,order,d,starts,ends):
    manager=auth(http,d,d["intake"])
    posted=offer(http,root,order,manager,d["manager"],starts,ends)
    assert posted.status_code==201,posted.text
    ident=posted.json()["assignmentId"]
    mechanic=auth(http,d,d["manager"])
    key=str(uuid4())
    accepted=http.post(root+f"/assignments/{ident}/accept",headers=mechanic,
        json={"requestKey":key})
    assert accepted.status_code==201,accepted.text
    return ident,key


def test_release_accepted_reassign_preserve_acceptance_bola_replay(monkeypatch):
    d,org,loc,order,foreign,http,root,starts,ends=ready(monkeypatch)
    ident,accept_key=accepted_order(http,root,order,d,starts,ends)
    manager=auth(http,d,d["intake"])
    queue=http.get(root+"/assignments")
    assert queue.status_code==200,queue.text
    assert queue.json()["releasable"][0]["assignmentId"]==ident
    assert queue.json()["releasable"][0]["status"]=="ACCEPTED"
    assert queue.json()["activeQueueLimited"] is False
    # Реальный route не вправе отдавать первые 100 как полный живой
    # список при переполнении. Две независимые manager очереди при этом
    # обязаны оставаться доступными для устранения зависших назначений.
    original_list=assignment_routes.list_assignments
    def overflow(*_args,**_kwargs):
        raise AssignmentConflict("LIMIT")
    assignment_routes.list_assignments=overflow
    try:
        full=http.get(root+"/assignments")
        assert full.status_code==200,full.text
        assert full.json()["items"]==[]
        assert full.json()["activeQueueLimited"] is True
        assert full.json()["releasable"][0]["assignmentId"]==ident
        assert full.json()["withdrawable"]==[]
        mechanic=auth(http,d,d["manager"])
        assert http.get(root+"/assignments").status_code==409
    finally:
        assignment_routes.list_assignments=original_list
    manager=auth(http,d,d["intake"])
    assert queue.json()["releasePage"]==0
    assert queue.json()["releaseHasMore"] is False
    assert queue.json()["withdrawable"]==[]
    assert len(queue.json()["items"])==1
    page=http.get(root+"/assignments?releasePage=1")
    assert page.status_code==200
    assert page.json()["releasable"]==[]
    assert page.json()["releasePage"]==1
    for invalid in ("-1","01","1001","abc"):
        bad=http.get(root+"/assignments",params={"releasePage":invalid})
        assert bad.status_code==422 and bad.json()["code"]=="INVALID_INPUT"
    assert http.get(root+"/assignments?releasePage=0&releasePage=1").status_code==422

    endpoint=root+f"/assignments/{ident}/release"
    assert http.post(endpoint,json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint,headers=manager,json={"requestKey":"bad"}).status_code==422
    assert http.post(root+f"/assignments/{uuid4()}/release",headers=manager,
                     json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint.replace(str(org),str(uuid4())),headers=manager,
                     json={"requestKey":str(uuid4())}).status_code==403
    assert http.post(endpoint.replace(str(loc),str(uuid4())),headers=manager,
                     json={"requestKey":str(uuid4())}).status_code==403
    other=auth(http,d,d["client_a"])
    assert http.get(root+"/assignments").status_code==403
    assert http.post(endpoint,headers=other,json={"requestKey":str(uuid4())}).status_code==403
    mechanic=auth(http,d,d["manager"])
    own=http.get(root+"/assignments")
    assert own.status_code==200 and own.json()["releasable"]==[]
    assert http.post(endpoint,headers=mechanic,json={"requestKey":str(uuid4())}).status_code==403

    with psycopg.connect(DSN) as db:
        before=db.execute("""SELECT accepted_at FROM auto_work_order_assignment
          WHERE id=%s""",(UUID(ident),)).fetchone()[0]
        assert before is not None

    manager=auth(http,d,d["intake"])
    key=str(uuid4())
    first=http.post(endpoint,headers=manager,json={"requestKey":key})
    assert first.status_code==201,first.text
    assert first.json()=={"ok":True,"replayed":False,"status":"RELEASED"}
    replay=http.post(endpoint,headers=manager,json={"requestKey":key})
    assert replay.status_code==200 and replay.json()["replayed"] is True
    assert http.post(endpoint,headers=manager,json={
        "requestKey":str(uuid4())}).status_code==409
    assert http.get(root+"/assignments").json()["releasable"]==[]

    # Оригинальный принятие/replay не оживляют RELEАSED.
    mechanic=auth(http,d,d["manager"])
    old=http.post(root+f"/assignments/{ident}/accept",headers=mechanic,
        json={"requestKey":accept_key})
    assert old.status_code==409,old.text

    manager=auth(http,d,d["intake"])
    second=offer(http,root,order,manager,d["other"],starts,ends)
    assert second.status_code==201,second.text
    next_id=second.json()["assignmentId"]
    assert next_id!=ident
    new_mechanic=auth(http,d,d["other"])
    response=http.post(root+f"/assignments/{next_id}/accept",
        headers=new_mechanic,json={"requestKey":str(uuid4())})
    assert response.status_code==201,response.text
    with psycopg.connect(DSN) as db:
        old=db.execute("""SELECT status,accepted_at,status_actor_id
          FROM auto_work_order_assignment WHERE id=%s""",(UUID(ident),)).fetchone()
        assert old==("RELEASED",before,d["intake"])
        assert db.execute("""SELECT action,actor_id
          FROM auto_work_order_assignment_event
          WHERE assignment_id=%s ORDER BY created_at,id""",(UUID(ident),)
        ).fetchall()==[("OFFERED",d["intake"]),("ACCEPTED",d["manager"]),
                      ("RELEASED",d["intake"])]
        assert db.execute("""SELECT count(*) FROM auto_work_order_assignment_event
          WHERE assignment_id=%s AND action='RELEASED'""",
            (UUID(ident),)).fetchone()==(1,)
        assert db.execute("""SELECT status FROM auto_work_order_assignment WHERE id=%s""",
                          (UUID(next_id),)).fetchone()==("ACCEPTED",)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_work_order_assignment_event
                  WHERE assignment_id=%s""",(UUID(ident),))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_assignment
                  SET accepted_at=NULL WHERE id=%s""",(UUID(ident),))


def test_cancelled_shift_and_proof_allow_safe_manager_release(monkeypatch):
    d,org,loc,order,foreign,http,root,starts,ends=ready(monkeypatch)
    ident,_=accepted_order(http,root,order,d,starts,ends)
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
    manager=auth(http,d,d["intake"])
    queue=http.get(root+"/assignments")
    assert queue.status_code==200,queue.text
    assert queue.json()["items"]==[]
    assert len(queue.json()["releasable"])==1
    key=str(uuid4())
    response=http.post(root+f"/assignments/{ident}/release",headers=manager,
        json={"requestKey":key})
    assert response.status_code==201,response.text
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""",(org,d["intake"]))
    assert http.post(root+f"/assignments/{ident}/release",headers=manager,
        json={"requestKey":key}).status_code==403
    assert http.get(root+"/assignments").status_code==403


def _approved_accepted(monkeypatch):
    d,org,loc,order,service,item,lot=approved_fixture(monkeypatch,with_material=False)
    starts=datetime.now(timezone.utc)+timedelta(hours=3)
    ends=starts+timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["manager"],starts-timedelta(minutes=5),
           ends+timedelta(minutes=5)))
    http=TestClient(app,base_url="https://127.0.0.1")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    ident,key=accepted_order(http,root,order,d,starts,ends)
    return d,org,loc,order,service,http,root,ident,key


def test_start_execution_blocks_release_and_sql_bypass(monkeypatch):
    d,org,loc,order,service,http,root,ident,key=_approved_accepted(monkeypatch)
    manager=auth(http,d,d["intake"])
    start=http.post(exec_url(org,loc,order)+"/start",headers=manager,
                    json={"requestKey":str(uuid4())})
    assert start.status_code==201,start.text
    assert http.get(root+"/assignments").json()["releasable"]==[]
    endpoint=root+f"/assignments/{ident}/release"
    rejected=http.post(endpoint,headers=manager,json={"requestKey":str(uuid4())})
    assert rejected.status_code==409,rejected.text
    assert rejected.json()["code"]=="WORK_ALREADY_STARTED"
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_assignment SET
                  status='RELEASED',status_actor_id=%s WHERE id=%s""",
                  (d["intake"],UUID(ident)))
        assert db.execute("""SELECT count(*) FROM auto_work_order_assignment_event
            WHERE assignment_id=%s AND action='RELEASED'""",
            (UUID(ident),)).fetchone()==(0,)
        assert db.execute("SELECT status FROM auto_work_order_assignment WHERE id=%s",
                          (UUID(ident),)).fetchone()==("ACCEPTED",)


def test_release_prevents_execution_start_until_next_accepted(monkeypatch):
    d,org,loc,order,service,http,root,ident,key=_approved_accepted(monkeypatch)
    manager=auth(http,d,d["intake"])
    released=http.post(root+f"/assignments/{ident}/release",headers=manager,
        json={"requestKey":str(uuid4())})
    assert released.status_code==201,released.text
    before=http.post(exec_url(org,loc,order)+"/start",headers=manager,
        json={"requestKey":str(uuid4())})
    assert before.status_code in (403,409),before.text
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_execution WHERE work_order_id=%s",
                          (order,)).fetchone()==(0,)
        assert db.execute("SELECT status FROM auto_work_order_assignment WHERE id=%s",
                          (UUID(ident),)).fetchone()==("RELEASED",)
    # После реальной смены и ACCEPTED нового исполнителя старт снова законен.
    starts=datetime.now(timezone.utc)+timedelta(hours=3)
    ends=starts+timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",(uuid4(),org,loc,d["other"],
              starts-timedelta(minutes=2),ends+timedelta(minutes=2)))
    posted=offer(http,root,order,manager,d["other"],starts,ends)
    assert posted.status_code==201,posted.text
    new_id=posted.json()["assignmentId"]
    other=auth(http,d,d["other"])
    assert http.post(root+f"/assignments/{new_id}/accept",
       headers=other,json={"requestKey":str(uuid4())}).status_code==201
    manager=auth(http,d,d["intake"])
    started=http.post(exec_url(org,loc,order)+"/start",headers=manager,
      json={"requestKey":str(uuid4())})
    assert started.status_code==201,started.text
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT status FROM auto_work_order_assignment
          WHERE id=%s""",(UUID(new_id),)).fetchone()==("ACCEPTED",)
        assert db.execute("""SELECT count(*) FROM auto_work_order_execution
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)


def test_release_races_with_execution_start_one_result_only(monkeypatch):
    d,org,loc,order,service,http,root,ident,key=_approved_accepted(monkeypatch)
    barrier=Barrier(2)
    def worker(mode):
        barrier.wait(timeout=15)
        with psycopg.connect(DSN) as db:
            try:
                if mode=="release":
                    return release_accepted_assignment(db,d["tokens"][d["intake"]],
                        org,loc,UUID(ident),uuid4())["status"]
                start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
                return "STARTED"
            except (AssignmentConflict,ExecutionConflict,psycopg.Error):
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(worker,["release","start"]))
    assert results.count("CONFLICT")==1,results
    assert set(results).intersection({"RELEASED","STARTED"})
    with psycopg.connect(DSN) as db:
        status=db.execute("SELECT status FROM auto_work_order_assignment WHERE id=%s",
                          (UUID(ident),)).fetchone()[0]
        started=db.execute("SELECT count(*) FROM auto_work_order_execution WHERE work_order_id=%s",
                           (order,)).fetchone()[0]
        assert (status,started) in (("RELEASED",0),("ACCEPTED",1))
        count=db.execute("""SELECT count(*) FROM auto_work_order_assignment_event
            WHERE assignment_id=%s AND action='RELEASED'""",
            (UUID(ident),)).fetchone()[0]
        assert count==(1 if status=="RELEASED" else 0)
