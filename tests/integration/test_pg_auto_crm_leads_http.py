"""PG17 + HTTPS: entire synthetic CRM inbound case→booking→terminal FSM."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_lead_service import create_lead, CrmConflict
from test_pg_auto_workshop_qc_http import scenario,client_setup,token_as
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm-leads"


def payload(owner,**extras):
    return {"requestKey":str(uuid4()),"intakeKey":str(uuid4()),
            "ownerId":str(owner),"sourceCode":"WALK_IN",**extras}


def post(http,path,headers,body):
    return http.post(path,headers=headers,json=body)


def test_http_crm_full_intake_to_appointment_won_retries_and_acl(monkeypatch):
    d,org,loc,bay,order,appointment,_,foreign,foreign_loc,_,_=scenario()
    with psycopg.connect(DSN) as db:
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
    http=client_setup(monkeypatch)
    base=url(org,loc)
    create_body=payload(d["intake"])
    assert http.get(base).status_code==403
    assert http.post(base,json=create_body).status_code==403
    intake=token_as(http,d,d["intake"])
    assert http.post(base,json=create_body).status_code==403  # CSRF
    first=post(http,base,intake,create_body)
    assert first.status_code==201,first.text
    body=first.json()
    ident=body["lead"]["id"]
    assert body["replayed"] is False and body["syntheticOnly"] is True
    assert body["lead"]["stage"]=="NEW" and body["lead"]["eventCount"]==1
    assert body["lead"]["appointmentId"] is None
    retry=post(http,base,intake,create_body)
    assert retry.status_code==200 and retry.json()["replayed"] is True
    duplicate=post(http,base,intake,{**create_body,"requestKey":str(uuid4())})
    assert duplicate.status_code==409
    assert post(http,base,intake,{**create_body,"intakeKey":str(uuid4())}).status_code==409
    assert post(http,base,intake,{**create_body,"ownerId":str(d["other"]),
        "intakeKey":str(uuid4()),"requestKey":str(uuid4())}).status_code==409
    items=http.get(base)
    assert items.status_code==200 and len(items.json()["items"])==1
    assert items.headers["cache-control"]=="no-store"
    assert items.json()["items"][0]["id"]==ident
    assert str(d["vehicle"]) not in items.text and str(d["link"]) not in items.text
    assert http.get(url(foreign,foreign_loc)).status_code==403
    assert http.get(url(org,foreign_loc)).status_code==403

    stage=base+"/"+ident+"/stage"
    booking=base+"/"+ident+"/booking"
    def step(name,reason=None):
        return {"requestKey":str(uuid4()),"stage":name,"reasonCode":reason}
    assert post(http,stage,intake,step("BOOKED")).status_code==409
    contacted=step("CONTACTED")
    assert post(http,stage,intake,contacted).status_code==201
    assert post(http,stage,intake,contacted).status_code==200
    assert post(http,stage,intake,step("WON")).status_code==409
    assert post(http,stage,intake,step("LOST")).status_code==422
    assert post(http,booking,intake,{"requestKey":str(uuid4()),
                                     "appointmentId":str(uuid4())}).status_code==409
    attach={"requestKey":str(uuid4()),"appointmentId":str(appointment)}
    connected=post(http,booking,intake,attach)
    assert connected.status_code==201,connected.text
    assert connected.json()["lead"]["appointmentId"]==str(appointment)
    assert post(http,booking,intake,attach).status_code==200
    assert post(http,booking,intake,{"requestKey":str(uuid4()),
        "appointmentId":str(appointment)}).status_code==409
    booked=step("BOOKED")
    assert post(http,stage,intake,booked).status_code==201
    won=step("WON")
    assert post(http,stage,intake,won).status_code==201
    assert post(http,stage,intake,won).status_code==200
    assert post(http,stage,intake,step("LOST","OTHER")).status_code==409
    final=http.get(base)
    assert final.status_code==200
    assert final.json()["items"][0]["stage"]=="WON"
    assert final.json()["items"][0]["eventCount"]==4
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT COUNT(*) FROM auto_crm_lead_stage_event WHERE lead_id=%s",
            (ident,),
        ).fetchone()[0]==4
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_crm_lead_stage_event SET stage='LOST' WHERE lead_id=%s",
                    (ident,),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_crm_lead WHERE id=%s",(ident,))
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (manager_grant,))
        # Revoking INTAKE access causes subsequent calls to fail immediately.
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s AND user_id=%s
                 AND role_code='INTAKE'""",
            (org,loc,d["intake"]),
        )
    assert http.get(base).status_code==403
    assert post(http,stage,intake,step("LOST","OTHER")).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert http.get(base).status_code==404


def test_crm_rejection_path_and_manager_role(monkeypatch):
    d,org,loc,bay,order,appointment,_,foreign,foreign_loc,_,_=scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MANAGER")
    http=client_setup(monkeypatch)
    manager=token_as(http,d,d["manager"])
    base=url(org,loc)
    created=post(http,base,manager,payload(d["manager"],sourceCode="WEB_FORM_TEST"))
    assert created.status_code==201,created.text
    ident=created.json()["lead"]["id"]
    lost=base+"/"+ident+"/stage"
    failure={"requestKey":str(uuid4()),"stage":"LOST","reasonCode":"CUSTOMER_DECLINED"}
    assert post(http,lost,manager,failure).status_code==201
    assert post(http,lost,manager,failure).status_code==200
    assert post(http,lost,manager,{"requestKey":str(uuid4()),
        "stage":"CONTACTED"}).status_code==409
    assert http.get(base).json()["items"][0]["reasonCode"]=="CUSTOMER_DECLINED"
    assert post(http,base,manager,payload(d["manager"],sourceCode="BAD")).status_code==422
    assert http.get(url(foreign,foreign_loc)).status_code==403


def test_parallel_intake_retry_serialized_and_no_duplicate_case(monkeypatch):
    d,org,loc,*_=scenario()
    key=uuid4()
    intake_key=uuid4()
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=15)
        with psycopg.connect(DSN) as db:
            result=create_lead(db,d["tokens"][d["intake"]],org,loc,
                owner_id=d["intake"],source_code="WALK_IN",
                intake_key=intake_key,request_key=key)
            return result["replayed"],result["lead"]["id"]
    answers=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers=list(pool.map(attempt,range(2)))
    assert sorted(x[0] for x in answers)==[False,True]
    assert answers[0][1]==answers[1][1]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_lead WHERE organization_id=%s AND location_id=%s",
            (org,loc),
        ).fetchone()[0]==1
        assert db.execute(
            "SELECT count(*) FROM auto_crm_lead_stage_event WHERE lead_id=%s",
            (answers[0][1],),
        ).fetchone()[0]==1
