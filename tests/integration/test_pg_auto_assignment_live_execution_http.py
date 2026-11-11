"""PG17 HTTPS + direct SQL: accepted shift revocation closes execution writes."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_work_order_execution_http import fixture, post, session, url
from test_pg_auto_work_order_assignment_http import auth


def _accepted(monkeypatch, *, with_material):
    d,org,loc,order,service,item,lot=fixture(monkeypatch,with_material=with_material)
    now=datetime.now(timezone.utc)
    starts=now+timedelta(hours=2)
    ends=starts+timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        shift=uuid4()
        db.execute("""INSERT INTO auto_staff_shift(
          id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (shift,org,loc,d["manager"],starts-timedelta(minutes=5),
           ends+timedelta(minutes=5)))
    http=TestClient(app,base_url="https://127.0.0.1")
    path=url(org,loc,order)
    manager=session(http,d,d["intake"])
    assert post(http,path+"/start",manager).status_code==201
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    offered=http.post(root+f"/orders/{order}/assignments",headers=manager,json={
      "mechanicId":str(d["manager"]),"startsAt":starts.isoformat(),
      "endsAt":ends.isoformat(),"requestKey":str(uuid4())})
    assert offered.status_code==201,offered.text
    mechanic=session(http,d,d["manager"])
    accepted=http.post(root+f"/assignments/{offered.json()['assignmentId']}/accept",
                       headers=mechanic,json={"requestKey":str(uuid4())})
    assert accepted.status_code==201,accepted.text
    return d,org,loc,order,service,item,lot,shift,http,path,root


def test_cancel_shift_refuses_labor_and_direct_pg17_inserts(monkeypatch):
    d,org,loc,order,service,item,lot,shift,http,path,root=_accepted(
      monkeypatch,with_material=False)
    mech=session(http,d,d["manager"])
    assert len(http.get(root+"/assignments").json()["items"])==1
    # Live cancellation does not rewrite the historical ACCEPTED audit.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s",(shift,))
    mech=session(http,d,d["manager"])
    assert http.get(root+"/assignments").json()["items"]==[]
    response=post(http,path+"/labor",mech,serviceId=str(service),actualMinutes=45)
    assert response.status_code==403,response.text
    assert post(http,path+"/finish",mech).status_code==403
    with psycopg.connect(DSN) as db:
        execution=db.execute("SELECT id FROM auto_work_order_execution "
                             "WHERE work_order_id=%s",(order,)).fetchone()[0]
        assert db.execute("SELECT status FROM auto_work_order_assignment "
                          "WHERE work_order_id=%s",(order,)).fetchone()==("ACCEPTED",)
        assert db.execute("SELECT count(*) FROM auto_work_order_labor_entry "
                          "WHERE execution_id=%s",(execution,)).fetchone()==(0,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_labor_entry
                  (id,execution_id,service_id,actor_id,actual_minutes,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s)""",
                  (uuid4(),execution,service,d["manager"],45,uuid4()))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_execution_finish
                  (id,execution_id,actor_id,request_key)
                  VALUES(%s,%s,%s,%s)""",
                  (uuid4(),execution,d["manager"],uuid4()))
        assert db.execute("SELECT count(*) FROM auto_work_order_execution_finish "
                          "WHERE execution_id=%s",(execution,)).fetchone()==(0,)


def test_cancel_shift_blocks_new_handoff_while_preserving_stock(monkeypatch):
    d,org,loc,order,service,item,lot,shift,http,path,root=_accepted(
      monkeypatch,with_material=True)
    warehouse=session(http,d,d["intake"])
    issued=post(http,path+"/materials/issue",warehouse,lotId=str(lot),quantity=1)
    assert issued.status_code==201,issued.text
    issue=issued.json()["issueId"]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s",(shift,))
    mechanic=session(http,d,d["manager"])
    denied=post(http,path+"/materials/receive",mechanic,issueId=issue)
    assert denied.status_code==403,denied.text
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_stock_mechanic_handoff "
                          "WHERE issue_id=%s",(issue,)).fetchone()==(0,)
        # Trusted handoff actor comes from the GUC before the assignment
        # trigger; the trigger itself must refuse an inactive assignment.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("SELECT set_config('roboport.mechanic_handoff_actor',%s,true)",
                           (str(d["manager"]),))
                db.execute("""INSERT INTO auto_stock_mechanic_handoff
                  (id,request_id,issue_id,received_by) VALUES(%s,%s,%s,%s)""",
                  (uuid4(),uuid4(),issue,d["manager"]))
        assert db.execute("SELECT count(*) FROM auto_stock_mechanic_handoff "
                          "WHERE issue_id=%s",(issue,)).fetchone()==(0,)


def test_still_active_assignment_allows_normal_work(monkeypatch):
    d,org,loc,order,service,_,_,shift,http,path,root=_accepted(
      monkeypatch,with_material=False)
    mech=session(http,d,d["manager"])
    completed=post(http,path+"/labor",mech,serviceId=str(service),actualMinutes=32)
    assert completed.status_code==201,completed.text
    assert post(http,path+"/finish",mech).status_code==201
    assert http.get(root+"/assignments").json()["items"][0]["status"]=="ACCEPTED"
