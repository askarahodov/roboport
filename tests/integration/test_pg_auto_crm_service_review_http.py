"""PG17: booked repair -> independent QC -> exactly one internal CRM review."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_followups import create_post_service_review
from roboport_api.auto_crm_leads import LeadConflict
from roboport_api.auto_work_order_execution import (
    start_execution,labor_execution,finish_execution,
)
from roboport_api.auto_workshop_qc import record_work_order_qc
from test_pg_auto_work_order_execution_http import fixture
from test_pg_baseline import DSN


def scope(org,loc,lead):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}"


def auth(http,d,actor):
    http.cookies.set("rp_session",d["tokens"][actor])
    return {"Origin":"https://127.0.0.1",
            "X-CSRF-Token":d["csrf"][actor]}


def setup(monkeypatch,finished=False):
    d,org,loc,order,service,_,_=fixture(monkeypatch,with_material=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    headers=auth(http,d,d["intake"])
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads"
    lead=http.post(base,headers=headers,json={
        "customerLinkId":str(d["link"]),
        "sourceCode":"WEB","requestKey":str(uuid4()),
    })
    assert lead.status_code==201,lead.text
    lead_id=lead.json()["leadId"]
    with psycopg.connect(DSN) as db:
        appointment=db.execute(
            "SELECT appointment_id FROM auto_work_order WHERE id=%s",
            (order,),
        ).fetchone()[0]
    qualify=http.post(scope(org,loc,lead_id)+"/stages",headers=headers,json={
        "stageCode":"QUALIFIED","requestKey":str(uuid4()),
    })
    assert qualify.status_code==201,qualify.text
    booked=http.post(scope(org,loc,lead_id)+"/stages",headers=headers,json={
        "stageCode":"BOOKED","appointmentId":str(appointment),
        "requestKey":str(uuid4()),
    })
    assert booked.status_code==201,booked.text
    if finished:
        complete(d,org,loc,order,service)
    return d,org,loc,order,service,http,lead_id


def complete(d,org,loc,order,service):
    with psycopg.connect(DSN) as db:
        start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
        labor_execution(db,d["tokens"][d["manager"]],org,loc,order,service,32,uuid4())
        finish_execution(db,d["tokens"][d["manager"]],org,loc,order,uuid4())
        record_work_order_qc(
            db,d["tokens"][d["other"]],org,loc,order,
            request_key=uuid4(),result="PASS",finding_code="NONE",
        )


def payload(order,key=None,days=14):
    return {"workOrderId":str(order),
            "dueAt":(datetime.now(timezone.utc)+timedelta(days=days))
                .replace(microsecond=0).isoformat(),
            "requestKey":str(key or uuid4())}


def test_post_service_review_full_manual_workflow_and_audit(monkeypatch):
    d,org,loc,order,service,http,lead=setup(monkeypatch)
    path=scope(org,loc,lead)+"/service-review"
    headers=auth(http,d,d["intake"])
    body=payload(order)
    assert http.post(path,json=body).status_code==403
    assert http.post(path,headers=headers,json=body).status_code==409
    complete(d,org,loc,order,service)

    fresh=http.post(path,headers=headers,json=body)
    assert fresh.status_code==201,fresh.text
    task=fresh.json()
    assert task["taskCode"]=="SERVICE_REVIEW"
    assert task["originWorkOrderId"]==str(order)
    assert task["status"]=="OPEN" and task["ownerId"]==str(d["intake"])
    assert task["event"] is None
    replay=http.post(path,headers=headers,json=body)
    assert replay.status_code==200,replay.text
    assert replay.json()["replayed"] is True and replay.json()["taskId"]==task["taskId"]
    assert http.post(path,headers=headers,json={
        **body,"dueAt":payload(order,days=20)["dueAt"],
    }).status_code==409
    assert http.post(path,headers=headers,json=payload(order)).status_code==409
    assert http.post(path,headers=headers,json=payload(uuid4())).status_code==409

    tasks=http.get(scope(org,loc,lead)+"/tasks")
    assert tasks.status_code==200,tasks.text
    assert tasks.json()["items"][0]["originWorkOrderId"]==str(order)
    end=http.post(
        scope(org,loc,lead)+"/tasks/"+task["taskId"]+"/events",
        headers=headers,json={"action":"DONE","requestKey":str(uuid4())},
    )
    assert end.status_code==201,end.text
    assert end.json()["status"]=="DONE"
    assert http.get(scope(org,loc,lead)+"/tasks").json()["items"][0]["status"]=="DONE"

    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_followup_task WHERE origin_work_order_id=%s",
            (order,),
        ).fetchone()[0]==1
        assert db.execute(
            "SELECT count(*) FROM auto_crm_followup_event WHERE task_id=%s",
            (UUID(task["taskId"]),),
        ).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_crm_followup_task SET origin_work_order_id=NULL WHERE id=%s",
                    (UUID(task["taskId"]),),
                )
    assert str(d["vehicle"]) not in fresh.text
    auth(http,d,d["client_a"])
    assert http.get(scope(org,loc,lead)+"/tasks").status_code==403
    assert http.post(path,headers=auth(http,d,d["client_a"]),json=body).status_code==403
    assert http.post(scope(uuid4(),loc,lead)+"/service-review",
                     headers=headers,json=body).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.post(path,headers=auth(http,d,d["intake"]),json=body).status_code==403
    assert http.get(scope(org,loc,lead)+"/tasks").status_code==403


def test_post_service_review_parallel_same_key_only_one_task(monkeypatch):
    d,org,loc,order,service,http,lead=setup(monkeypatch,finished=True)
    due=(datetime.now(timezone.utc)+timedelta(days=10)).replace(microsecond=0)
    key=uuid4()
    barrier=Barrier(2)
    def create(_):
        barrier.wait(timeout=20)
        with psycopg.connect(DSN) as db:
            return create_post_service_review(
                db,d["tokens"][d["intake"]],org,loc,UUID(lead),key,order,due,
            )
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(create,range(2)))
    assert result[0]["taskId"]==result[1]["taskId"]
    assert sorted(x["replayed"] for x in result)==[False,True]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_followup_task WHERE origin_work_order_id=%s",
            (order,),
        ).fetchone()[0]==1


def test_post_service_review_rejects_invalid_payload_and_terminal_lost(monkeypatch):
    d,org,loc,order,service,http,lead=setup(monkeypatch,finished=True)
    path=scope(org,loc,lead)+"/service-review"
    headers=auth(http,d,d["intake"])
    body=payload(order)
    for bad in [
        {**body,"workOrderId":"not-a-uuid"},
        {**body,"dueAt":"2030-01-01T10:00:00"},
        {**body,"extra":"not-allowed"},
    ]:
        assert http.post(path,headers=headers,json=bad).status_code==422
    assert http.post(scope(org,loc,uuid4())+"/service-review",
                     headers=headers,json=body).status_code==403
