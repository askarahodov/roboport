"""PG17/HTTPS: synthetic CRM follow-up task, ownership, due time and immutable audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_followups import create_followup, resolve_followup
from roboport_api.auto_crm_leads import LeadConflict
from test_pg_auto_bay_occupancy_http import _fixture
from test_pg_baseline import DSN


def scope(org,loc,lead):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/tasks"


def session(http,d,who):
    http.cookies.set("rp_session",d["tokens"][who])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][who]}


def setup(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    headers=session(http,d,d["intake"])
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads"
    lead=http.post(root,headers=headers,json={
        "customerLinkId":str(d["link"]),
        "sourceCode":"WEB","requestKey":str(uuid4()),
    })
    assert lead.status_code==201,lead.text
    return d,org,loc,bay,foreign,foreign_loc,http,lead.json()["leadId"]


def test_full_followup_to_resolution_http_audit_ownership_and_revoke(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    path=scope(org,loc,lead)
    due=(datetime.now(timezone.utc)+timedelta(days=8)).replace(microsecond=0)
    payload={"taskCode":"CALL_BACK","dueAt":due.isoformat(),"requestKey":str(uuid4())}
    headers=session(http,d,d["intake"])
    assert http.get(path).status_code==200
    assert http.get(path).json()["items"]==[]
    assert http.post(path,json=payload).status_code==403
    created=http.post(path,headers=headers,json=payload)
    assert created.status_code==201,created.text
    row=created.json()
    assert row["status"]=="OPEN" and row["ownerId"]==str(d["intake"])
    assert row["creatorId"]==str(d["intake"]) and row["event"] is None
    task=row["taskId"]
    listed=http.get(path)
    assert listed.status_code==200
    assert listed.headers["cache-control"]=="no-store"
    assert len(listed.json()["items"])==1
    again=http.post(path,headers=headers,json=payload)
    assert again.status_code==200 and again.json()["replayed"] is True
    assert again.json()["taskId"]==task
    assert http.post(path,headers=headers,json={
        **payload,"taskCode":"SERVICE_REVIEW",
    }).status_code==409
    assert http.post(path,headers=headers,json={
        **payload,"requestKey":str(uuid4()),"taskCode":"BOGUS",
    }).status_code==422
    assert http.post(path,headers=headers,json={
        **payload,"requestKey":str(uuid4()),"dueAt":"2030-01-01T10:00:00",
    }).status_code==422
    resolution=path+"/"+task+"/events"
    completion={"action":"DONE","requestKey":str(uuid4())}
    done=http.post(resolution,headers=headers,json=completion)
    assert done.status_code==201,done.text
    assert done.json()["status"]=="DONE" and done.json()["event"]["action"]=="DONE"
    assert http.post(resolution,headers=headers,json=completion).status_code==200
    assert http.post(resolution,headers=headers,json={
        **completion,"action":"CANCELED",
    }).status_code==409
    assert http.post(resolution,headers=headers,json={
        "action":"CANCELED","requestKey":str(uuid4()),
    }).status_code==409
    assert len(http.get(path).json()["items"])==1
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM auto_crm_followup_task WHERE lead_id=%s",
            (UUID(lead),),
        ).fetchone()==(1,)
        assert db.execute(
            """SELECT COUNT(*) FROM auto_crm_followup_event e
               JOIN auto_crm_followup_task t ON t.id=e.task_id WHERE t.lead_id=%s""",
            (UUID(lead),),
        ).fetchone()==(1,)
        for sql,params in [
            ("DELETE FROM auto_crm_followup_task WHERE id=%s",(UUID(task),)),
            ("UPDATE auto_crm_followup_task SET task_code='SERVICE_REVIEW' WHERE id=%s",
             (UUID(task),)),
            ("DELETE FROM auto_crm_followup_event WHERE task_id=%s",(UUID(task),)),
        ]:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,params)
    assert str(d["vehicle"]) not in listed.text
    assert http.get(scope(foreign,foreign_loc,lead)).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert http.get(path).status_code==403
    assert http.post(resolution,headers=headers,json={
        "action":"CANCELED","requestKey":str(uuid4()),
    }).status_code==403
    session(http,d,d["manager"])
    assert http.get(path).status_code==200  # scoped MANAGER
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_vehicle_customer_link SET revoked_at=now()
               WHERE id=%s""",
            (d["link"],),
        )
    assert http.get(path).status_code==403
    assert http.post(path,headers=session(http,d,d["intake"]),json={
        **payload,"requestKey":str(uuid4()),
    }).status_code==403


def test_terminal_lead_and_revoked_manager_role_fail_closed(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    path=scope(org,loc,lead)
    due=(datetime.now(timezone.utc)+timedelta(days=7)).isoformat()
    intake=session(http,d,d["intake"])
    task=http.post(path,headers=intake,json={
        "taskCode":"CHECK_BOOKING","dueAt":due,"requestKey":str(uuid4()),
    })
    assert task.status_code==201,task.text
    manager=session(http,d,d["manager"])
    done=http.post(path+"/"+task.json()["taskId"]+"/events",headers=manager,json={
        "action":"CANCELED","requestKey":str(uuid4()),
    })
    assert done.status_code==201,done.text
    assert done.json()["status"]=="CANCELED"
    # A lead's terminal stage blocks new tasks. Existing task evidence is
    # retained and readable only while role+ownership proof remains live.
    stage=session(http,d,d["intake"])
    r=http.post(path.removesuffix("/tasks")+"/stages",headers=stage,json={
        "stageCode":"LOST","reasonCode":"TIMING","requestKey":str(uuid4()),
    })
    assert r.status_code==201,r.text
    new=http.post(path,headers=stage,json={
        "taskCode":"CALL_BACK","dueAt":due,"requestKey":str(uuid4()),
    })
    assert new.status_code==409
    assert http.get(path).json()["items"][0]["status"]=="CANCELED"
    session(http,d,d["manager"])
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='MANAGER'""",
            (org,loc,d["manager"]),
        )
    assert http.get(path).status_code==403


def test_concurrent_followup_create_and_competing_terminal_events(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    token=d["tokens"][d["intake"]]
    due=(datetime.now(timezone.utc)+timedelta(days=9)).replace(microsecond=0)
    key=uuid4()
    barrier=Barrier(2)
    def create(_):
        barrier.wait(timeout=10)
        with psycopg.connect(DSN) as db:
            return create_followup(db,token,org,loc,UUID(lead),key,"CALL_BACK",due)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(create,range(2)))
    assert responses[0]["taskId"]==responses[1]["taskId"]
    assert sorted(x["replayed"] for x in responses)==[False,True]
    task=UUID(responses[0]["taskId"])
    barrier=Barrier(2)
    def end(action):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                return resolve_followup(
                    db,token,org,loc,UUID(lead),task,uuid4(),action,
                )["status"]
        except LeadConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(end,["DONE","CANCELED"]))
    assert "CONFLICT" in outcomes and len(outcomes)==2
    assert any(x in ("DONE","CANCELED") for x in outcomes)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM auto_crm_followup_event WHERE task_id=%s",
            (task,),
        ).fetchone()==(1,)
