"""PG17/HTTPS CRM workflow: stage policy, reassignment, Kanban and inert consent outbox."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import psycopg
import pytest

from test_pg_auto_crm_leads_http import _fixture,session
from test_pg_baseline import DSN

def crm(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm"

def lead_payload(d):
    return {"customerLinkId":str(d["link"]),"sourceCode":"WEB","requestKey":str(uuid4())}

def config_payload(config,version=0,key=None):
    return {"expectedRevision":version,"requestKey":str(key or uuid4()),"config":config}


def test_workflow_config_assignment_kanban_and_reassigned_followup(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)
    intake=session(http,d,d["intake"])
    manager=session(http,d,d["manager"])
    intake=session(http,d,d["intake"])
    created=http.post(base+"/leads",headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead_id=created.json()["leadId"]
    detail=base+"/leads/"+lead_id

    initial=http.get(base+"/workflow",headers=manager)
    assert initial.status_code==200,initial.text
    assert initial.json()["canConfigure"] is True
    assert initial.json()["version"]==0
    intake=session(http,d,d["intake"])
    assert http.get(base+"/workflow",headers=intake).json()["canConfigure"] is False

    config=initial.json()["config"]
    stages=config["stages"]
    lost=next(x for x in stages if x["code"]=="LOST")
    lost["label"]="Не состоялось"
    lost["position"]=5
    lost["lossReasons"]=["TIMING"]
    config_key=uuid4()
    manager=session(http,d,d["manager"])
    saved=http.post(base+"/workflow",headers=manager,json=config_payload(config,key=config_key))
    assert saved.status_code==201,saved.text
    assert saved.json()["version"]==1
    manager=session(http,d,d["manager"])
    replay=http.post(base+"/workflow",headers=manager,json=config_payload(config,key=config_key))
    assert replay.status_code==200 and replay.json()["replayed"] is True
    manager=session(http,d,d["manager"])
    stale=http.post(base+"/workflow",headers=manager,json=config_payload(config,version=0))
    assert stale.status_code==409
    manager=session(http,d,d["manager"])
    bad_config={"stages":[dict(x) for x in config["stages"]]}
    next(x for x in bad_config["stages"] if x["code"]=="LOST")["lossReasons"]=[{"bad":"type"}]
    invalid=http.post(base+"/workflow",headers=manager,json=config_payload(bad_config,version=1))
    assert invalid.status_code==422
    intake=session(http,d,d["intake"])
    forbidden=http.post(base+"/workflow",headers=intake,json=config_payload(config,version=1))
    assert forbidden.status_code==403

    # Create an internal follow-up while the lead belongs to INTAKE.
    due=(datetime.now(timezone.utc)+timedelta(days=5)).isoformat()
    task=http.post(detail+"/tasks",headers=intake,json={
        "requestKey":str(uuid4()),"taskCode":"CALL_BACK","dueAt":due,
    })
    assert task.status_code==201,task.text
    assert task.json()["ownerId"]==str(d["intake"])

    assignment_key=uuid4()
    manager=session(http,d,d["manager"])
    assigned=http.post(detail+"/assignment",headers=manager,json={
        "assigneeId":str(d["manager"]),"requestKey":str(assignment_key),
    })
    assert assigned.status_code==201,assigned.text
    assert assigned.json()["ownerId"]==str(d["manager"])
    assert len(assigned.json()["assignmentEvents"])==1
    replay=http.post(detail+"/assignment",headers=manager,json={
        "assigneeId":str(d["manager"]),"requestKey":str(assignment_key),
    })
    assert replay.status_code==200 and replay.json()["replayed"] is True
    intake=session(http,d,d["intake"])
    assert http.get(detail,headers=intake).status_code==403
    manager=session(http,d,d["manager"])
    manager_detail=http.get(detail,headers=manager)
    assert manager_detail.status_code==200
    assert manager_detail.json()["assignmentEvents"][0]["assigneeId"]==str(d["manager"])

    manager=session(http,d,d["manager"])
    manager_inbox=http.get(base+"/inbox",headers=manager)
    assert manager_inbox.status_code==200,manager_inbox.text
    assert any(item["leadId"]==lead_id and item["taskId"]==task.json()["taskId"]
               for item in manager_inbox.json()["items"])
    intake=session(http,d,d["intake"])
    intake_inbox=http.get(base+"/inbox",headers=intake)
    assert intake_inbox.status_code==200,intake_inbox.text
    assert all(item["leadId"]!=lead_id for item in intake_inbox.json()["items"])

    manager=session(http,d,d["manager"])
    tasks=http.get(detail+"/tasks",headers=manager)
    assert tasks.status_code==200,tasks.text
    assert tasks.json()["items"][0]["ownerId"]==str(d["manager"])
    manager=session(http,d,d["manager"])
    board=http.get(base+"/kanban",headers=manager)
    assert board.status_code==200,board.text
    assert board.json()["canConfigure"] is True
    assert any(x["leadId"]==lead_id and x["ownerId"]==str(d["manager"])
               for x in board.json()["items"])
    intake=session(http,d,d["intake"])
    assert all(x["leadId"]!=lead_id for x in http.get(base+"/kanban",headers=intake).json()["items"])
    manager=session(http,d,d["manager"])
    assert http.get(crm(foreign,foreign_loc)+"/kanban",headers=manager).status_code==403

    # Configured loss reasons are enforced by PostgreSQL, not only by the UI.
    manager=session(http,d,d["manager"])
    bad=http.post(detail+"/stages",headers=manager,json={
        "stageCode":"LOST","reasonCode":"PRICE","requestKey":str(uuid4()),
    })
    assert bad.status_code==409
    good=http.post(detail+"/stages",headers=manager,json={
        "stageCode":"LOST","reasonCode":"TIMING","requestKey":str(uuid4()),
    })
    assert good.status_code==201,good.text
    assert good.json()["status"]=="LOST"
    assert http.post(detail+"/stages",headers=manager,json={
        "stageCode":"QUALIFIED","requestKey":str(uuid4()),
    }).status_code==409

    with psycopg.connect(DSN) as db:
        for sql,params in (
            ("UPDATE auto_crm_lead_assignment_event SET assignee_id=%s WHERE lead_id=%s",
             (d["intake"],lead_id)),
            ("DELETE FROM auto_crm_lead_assignment_event WHERE lead_id=%s",(lead_id,)),
            ("UPDATE auto_crm_workflow_config_event SET version=version+1 WHERE organization_id=%s",
             (org,)),
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,params)


def test_synthetic_service_consent_gates_inert_outbox_and_unsubscribe(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)
    intake=session(http,d,d["intake"])
    created=http.post(base+"/leads",headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead_id=created.json()["leadId"]
    detail=base+"/leads/"+lead_id
    manager=session(http,d,d["manager"])
    client=session(http,d,d["client_a"])
    consent_url="/api/v1/dev/account/crm-contact-consent"

    client=session(http,d,d["client_a"])
    initial=http.get(consent_url,headers=client)
    assert initial.status_code==200,initial.text
    assert initial.json()=={
        "syntheticOnly":True,"purposeCode":"SERVICE_UPDATE",
        "legalConsentVerified":False,"sendAllowed":False,
        "channels":{"CALL":False,"EMAIL":False,"SMS":False},
        "revisions":{"CALL":0,"EMAIL":0,"SMS":0},
    }
    manager=session(http,d,d["manager"])
    denied=http.post(detail+"/mock-messages",headers=manager,json={
        "channelCode":"CALL","templateCode":"SERVICE_UPDATE","requestKey":str(uuid4()),
    })
    assert denied.status_code==409

    key=uuid4()
    client=session(http,d,d["client_a"])
    opted=http.post(consent_url,headers=client,json={
        "channelCode":"CALL","enabled":True,"expectedRevision":0,
        "requestKey":str(key),
    })
    assert opted.status_code==201,opted.text
    assert opted.json()["sendAllowed"] is False
    assert opted.json()["legalConsentVerified"] is False
    assert opted.json()["channels"]["CALL"] is True

    manager=session(http,d,d["manager"])
    staff=http.get(detail+"/contact-consent",headers=manager)
    assert staff.status_code==200,staff.text
    assert staff.json()["channels"]["CALL"] is True
    assert staff.json()["legalConsentVerified"] is False
    mock_key=uuid4()
    manager=session(http,d,d["manager"])
    mock=http.post(detail+"/mock-messages",headers=manager,json={
        "channelCode":"CALL","templateCode":"BOOKING_REMINDER",
        "requestKey":str(mock_key),
    })
    assert mock.status_code==201,mock.text
    assert mock.json()["status"]=="MOCK_ONLY"
    assert mock.json()["sendAllowed"] is False
    assert "recipient" not in mock.text.lower() and "body" not in mock.text.lower()
    manager=session(http,d,d["manager"])
    replay=http.post(detail+"/mock-messages",headers=manager,json={
        "channelCode":"CALL","templateCode":"BOOKING_REMINDER",
        "requestKey":str(mock_key),
    })
    assert replay.status_code==200 and replay.json()["replayed"] is True
    manager=session(http,d,d["manager"])
    queue=http.get(detail+"/mock-messages",headers=manager)
    assert queue.status_code==200
    assert queue.json()["items"][0]["status"]=="MOCK_ONLY"
    assert queue.json()["sendAllowed"] is False

    client=session(http,d,d["client_a"])
    optout=http.post(consent_url,headers=client,json={
        "channelCode":"CALL","enabled":False,"expectedRevision":1,
        "requestKey":str(uuid4()),
    })
    assert optout.status_code==201,optout.text
    manager=session(http,d,d["manager"])
    staff_after=http.get(detail+"/contact-consent",headers=manager)
    assert staff_after.json()["channels"]["CALL"] is False
    manager=session(http,d,d["manager"])
    suppressed=http.get(detail+"/mock-messages",headers=manager)
    assert suppressed.json()["items"][0]["status"]=="SUPPRESSED"
    manager=session(http,d,d["manager"])
    assert http.post(detail+"/mock-messages",headers=manager,json={
        "channelCode":"CALL","templateCode":"SERVICE_UPDATE",
        "requestKey":str(uuid4()),
    }).status_code==409

    # A second active account must also opt in; no partial recipient set.
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (d["client_b"],d["ca"]))
    manager=session(http,d,d["manager"])
    assert http.get(detail+"/contact-consent",headers=manager).json()["channels"]["CALL"] is False
    second=session(http,d,d["client_b"])
    second_optin=http.post(consent_url,headers=second,json={
        "channelCode":"CALL","enabled":True,"expectedRevision":0,
        "requestKey":str(uuid4()),
    })
    assert second_optin.status_code==201,second_optin.text
    first_again=session(http,d,d["client_a"])
    first_optin=http.post(consent_url,headers=first_again,json={
        "channelCode":"CALL","enabled":True,"expectedRevision":2,
        "requestKey":str(uuid4()),
    })
    assert first_optin.status_code==201,first_optin.text
    manager=session(http,d,d["manager"])
    assert http.get(detail+"/contact-consent",headers=manager).json()["channels"]["CALL"] is True
    # A prior mock remains suppressed after a later re-consent; consent
    # snapshots are immutable and never silently re-activate old outbox rows.
    assert http.get(detail+"/mock-messages",headers=manager).json()["items"][0]["status"]=="SUPPRESSED"

    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_crm_contact_consent_event SET enabled=true WHERE user_id=%s",
                    (d["client_a"],),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_crm_mock_outbox WHERE lead_id=%s",(lead_id,))
