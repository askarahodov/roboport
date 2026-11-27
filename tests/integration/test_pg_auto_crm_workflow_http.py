"""PG17/HTTPS CRM workflow: stage policy, reassignment, Kanban and inert consent outbox."""
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4

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

    manager=session(http,d,d["manager"])
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
        "channels":{"CALL":False,"EMAIL":False,"SMS":False,"MESSENGER":False},
        "revisions":{"CALL":0,"EMAIL":0,"SMS":0,"MESSENGER":0},
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


def test_concurrent_workflow_config_updates_serialize_without_deadlock(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from roboport_api import auto_crm_workflow

    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    manager_token=d["tokens"][d["manager"]]
    with psycopg.connect(DSN) as db:
        current=auto_crm_workflow.get_workflow_config(db,manager_token,org,loc)
    config=current["config"]
    barrier=Barrier(2)

    def update(key):
        try:
            with psycopg.connect(DSN) as db:
                barrier.wait(timeout=10)
                return auto_crm_workflow.set_workflow_config(
                    db,manager_token,org,loc,0,key,config
                )
        except auto_crm_workflow.LeadConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(update,uuid4()) for _ in range(2)]
        results=[future.result(timeout=20) for future in futures]
    assert sum(isinstance(result,dict) and result["version"]==1 for result in results)==1
    assert sum(result=="STALE_REVISION" for result in results)==1


def test_parallel_mock_outbox_for_distinct_leads_sharing_proof_has_no_deadlock(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier,Lock
    from roboport_api import auto_crm_consent

    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)
    intake=session(http,d,d["intake"])
    first=http.post(base+"/leads",headers=intake,json=lead_payload(d))
    assert first.status_code==201,first.text
    intake=session(http,d,d["intake"])
    second_payload=lead_payload(d)
    second_payload["sourceCode"]="PHONE"
    second=http.post(base+"/leads",headers=intake,json=second_payload)
    assert second.status_code==201,second.text
    first_lead=UUID(first.json()["leadId"])
    second_lead=UUID(second.json()["leadId"])

    with psycopg.connect(DSN) as db:
        auto_crm_consent.change_customer_consent(
            db,d["tokens"][d["client_a"]],"CALL",True,0,uuid4()
        )

    barrier=Barrier(2)
    seen_connections=set()
    seen_lock=Lock()
    original=auto_crm_consent._account_users

    def synchronized_account_users(conn,customer_id):
        users=original(conn,customer_id)
        with seen_lock:
            first_call=id(conn) not in seen_connections
            seen_connections.add(id(conn))
        if first_call:
            barrier.wait(timeout=10)
        return users

    monkeypatch.setattr(auto_crm_consent,"_account_users",synchronized_account_users)
    manager_token=d["tokens"][d["manager"]]

    def create(lead_id):
        with psycopg.connect(DSN) as db:
            return auto_crm_consent.create_mock_message(
                db,manager_token,org,loc,lead_id,uuid4(),"CALL","BOOKING_REMINDER"
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(create,lead) for lead in (first_lead,second_lead)]
        results=[future.result(timeout=20) for future in futures]
    assert all(result["status"]=="MOCK_ONLY" and result["sendAllowed"] is False
               for result in results)


def test_complete_mock_history_over_100_and_live_suppression(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)
    intake=session(http,d,d["intake"])
    created=http.post(base+"/leads",headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead=created.json()["leadId"]
    path=base+"/leads/"+lead+"/mock-messages"
    consent="/api/v1/dev/account/crm-contact-consent"
    client=session(http,d,d["client_a"])
    assert http.post(consent,headers=client,json={
        "channelCode":"CALL","enabled":True,"expectedRevision":0,"requestKey":str(uuid4())
    }).status_code==201
    manager=session(http,d,d["manager"])
    ids=set()
    for i in range(102):
        created=http.post(path,headers=manager,json={
            "channelCode":"CALL","templateCode":"SERVICE_UPDATE","requestKey":str(uuid4())})
        assert created.status_code==201,created.text
        ids.add(created.json()["messageId"])
    first=http.get(path).json()
    assert len(first["items"])==100
    cursor=first["nextCursor"]
    tail=http.get(path,params={"cursor":cursor}).json()
    assert len(tail["items"])==2 and "nextCursor" not in tail
    assert {x["messageId"] for x in first["items"]+tail["items"]}==ids
    assert all(x["status"]=="MOCK_ONLY" for x in tail["items"])
    assert http.get(path,params={"cursor":cursor+"x"}).status_code==422
    assert http.get(path+"?cursor="+cursor+"&cursor="+cursor).status_code==422
    assert http.get(crm(foreign,foreign_loc)+"/leads/"+lead+"/mock-messages",
                    params={"cursor":cursor}).status_code==403
    client=session(http,d,d["client_a"])
    assert http.post(consent,headers=client,json={
        "channelCode":"CALL","enabled":False,"expectedRevision":1,"requestKey":str(uuid4())
    }).status_code==201
    session(http,d,d["manager"])
    suppressed=http.get(path,params={"cursor":cursor})
    assert suppressed.status_code==200,suppressed.text
    assert all(x["status"]=="SUPPRESSED" for x in suppressed.json()["items"])
    session(http,d,d["intake"])
    assert http.get(path,params={"cursor":cursor}).status_code==422
    session(http,d,d["client_a"])
    assert http.get(path,params={"cursor":cursor}).status_code==403


def test_four_offline_adapters_require_separate_consent_and_replay_stable_receipt(monkeypatch):
    from hashlib import sha256
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)
    intake=session(http,d,d["intake"])
    created=http.post(base+"/leads",headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead=created.json()["leadId"]
    path=base+"/leads/"+lead+"/mock-messages"
    consent="/api/v1/dev/account/crm-contact-consent"
    ids=set()
    for channel,adapter in (("CALL","MOCK_IP_TELEPHONY"),("EMAIL","MOCK_EMAIL"),
                            ("SMS","MOCK_SMS"),("MESSENGER","MOCK_MESSENGER")):
        payload={"channelCode":channel,"templateCode":"SERVICE_UPDATE","requestKey":str(uuid4())}
        manager=session(http,d,d["manager"])
        assert http.post(path,headers=manager,json=payload).status_code==409
        owner=session(http,d,d["client_a"])
        changed=http.post(consent,headers=owner,json={"channelCode":channel,"enabled":True,
                            "expectedRevision":0,"requestKey":str(uuid4())})
        assert changed.status_code==201,changed.text
        manager=session(http,d,d["manager"])
        first=http.post(path,headers=manager,json=payload)
        assert first.status_code==201,first.text
        record=first.json()
        assert record["messageId"] not in ids
        ids.add(record["messageId"])
        receipt=record["adapter"]
        assert receipt=={"schemaVersion":1,"adapterCode":adapter,
            "receiptSha256":sha256(("crm-mock-v1|"+record["messageId"]+"|"+channel+
                                  "|SERVICE_UPDATE|"+adapter).encode("ascii")).hexdigest(),
            "networkAllowed":False,"delivered":False}
        assert record["sendAllowed"] is False
        replay=http.post(path,headers=manager,json=payload)
        assert replay.status_code==200,replay.text
        assert replay.json()["adapter"]==receipt
        queue=http.get(path,headers=manager).json()["items"]
        assert next(x for x in queue if x["messageId"]==record["messageId"])["adapter"]==receipt
        owner=session(http,d,d["client_a"])
        assert http.post(consent,headers=owner,json={"channelCode":channel,"enabled":False,
                        "expectedRevision":1,"requestKey":str(uuid4())}).status_code==201
        manager=session(http,d,d["manager"])
        assert http.post(path,headers=manager,json=payload).status_code==409
        suppressed=next(x for x in http.get(path,headers=manager).json()["items"]
                        if x["messageId"]==record["messageId"])
        assert suppressed["status"]=="SUPPRESSED" and suppressed["adapter"] is None
        owner=session(http,d,d["client_a"])
        assert http.post(consent,headers=owner,json={"channelCode":channel,"enabled":True,
                        "expectedRevision":2,"requestKey":str(uuid4())}).status_code==201
        manager=session(http,d,d["manager"])
        assert http.post(path,headers=manager,json=payload).status_code==409
        assert next(x for x in http.get(path,headers=manager).json()["items"]
                    if x["messageId"]==record["messageId"])["adapter"] is None
    manager=session(http,d,d["manager"])
    assert http.get(path.replace(str(loc),str(foreign_loc)),headers=manager).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_crm_mock_outbox WHERE lead_id=%s",(lead,)).fetchone()==(4,)


def test_inbound_interaction_journal_concurrent_retry_owner_audit_pagination_and_revoke(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from roboport_api.auto_crm_interactions import record_interaction
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)+"/leads"
    intake=session(http,d,d["intake"])
    created=http.post(base,headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead_id=UUID(created.json()["leadId"])
    detail=base+"/"+str(lead_id)
    endpoint=detail+"/interactions"
    key=uuid4()
    due=datetime.now(timezone.utc)+timedelta(days=2)
    payload={"requestKey":str(key),"channelCode":"CALL","outcomeCode":"CALLBACK_REQUIRED",
             "callbackDueAt":due.isoformat(),"expectedActorId":str(d["intake"])}
    assert http.post(endpoint,json=payload).status_code==403
    assert http.post(endpoint,headers=intake,json={**payload,"phone":"synthetic"}).status_code==422
    assert http.post(endpoint,headers=intake,json={**payload,"channelCode":"UNKNOWN"}).status_code==422
    assert http.get(endpoint).json()["items"]==[]
    assert http.post(endpoint,headers=intake,json={**payload,"expectedActorId":str(d["manager"])}).status_code==403
    expired_due=(due-timedelta(days=3)).isoformat()
    rejected=http.post(endpoint,headers=intake,json={**payload,"callbackDueAt":expired_due})
    assert rejected.status_code==422,rejected.text
    receipt=rejected.json()
    assert receipt["code"]=="CALLBACK_DEADLINE_EXPIRED" and receipt["requestKey"]==str(key)
    assert receipt["viewerId"]==str(d["intake"]) and receipt["leadId"]==str(lead_id)
    assert datetime.fromisoformat(receipt["callbackDueAt"])==datetime.fromisoformat(expired_due)
    assert receipt["sendAllowed"] is False
    assert http.get(detail+"/tasks").json()["items"]==[]
    barrier=Barrier(2)
    def concurrent_write():
        with psycopg.connect(DSN) as db:
            barrier.wait(timeout=10)
            return record_interaction(db,d["tokens"][d["intake"]],org,loc,lead_id,key,
                                      "CALL","CALLBACK_REQUIRED",due,d["intake"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(concurrent_write) for _ in range(2)]
        one,two=[future.result(timeout=20) for future in futures]
    assert sorted([one["replayed"],two["replayed"]])==[False,True]
    assert one["item"]==two["item"]
    original=one["item"]
    assert original["ownerId"]==original["actorId"]==str(d["intake"])
    assert original["sourceCode"]=="WEB" and original["direction"]=="INBOUND_SIM"
    assert original["callbackTaskId"] and original["callbackDueAt"]==due.isoformat()
    tasks=http.get(detail+"/tasks").json()["items"]
    assert len(tasks)==1 and tasks[0]["taskId"]==original["callbackTaskId"] and tasks[0]["taskCode"]=="CALL_BACK"
    assert http.get(crm(org,loc)+"/inbox").json()["items"][0]["taskId"]==original["callbackTaskId"]
    replay=http.post(endpoint,headers=intake,json=payload)
    assert replay.status_code==200 and replay.json()["item"]==original
    assert replay.json()["sendAllowed"] is False
    assert http.post(endpoint,headers=intake,json={**payload,"callbackDueAt":(due+timedelta(days=1)).isoformat()}).status_code==409
    assert http.post(endpoint,headers=intake,json={**payload,"outcomeCode":"DECLINED","callbackDueAt":None}).status_code==409
    assert http.get(detail).json()["status"]=="NEW"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_crm_mock_outbox WHERE lead_id=%s",(lead_id,)).fetchone()==(0,)
        for n in range(26):
            record_interaction(db,d["tokens"][d["intake"]],org,loc,lead_id,uuid4(),
                               ("EMAIL","SMS","MESSENGER","WALK_IN")[n%4],"INFORMATION_REQUESTED")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_crm_interaction SET outcome_code='DECLINED' WHERE lead_id=%s",(lead_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_crm_interaction WHERE lead_id=%s",(lead_id,))
    first=http.get(endpoint)
    assert first.status_code==200 and first.headers["cache-control"]=="no-store"
    page=first.json()
    assert len(page["items"])==25 and page["nextCursor"]
    second=http.get(endpoint,params={"cursor":page["nextCursor"]}).json()
    assert len(second["items"])==2 and second["nextCursor"] is None
    all_items=page["items"]+second["items"]
    assert len({x["interactionId"] for x in all_items})==27 and original in all_items
    assert http.get(endpoint,params={"cursor":"bogus"}).status_code==422
    assert http.get(endpoint.replace(str(loc),str(foreign_loc))).status_code==403
    manager=session(http,d,d["manager"])
    assigned=http.post(detail+"/assignment",headers=manager,json={
        "assigneeId":str(d["manager"]),"requestKey":str(uuid4())})
    assert assigned.status_code==201,assigned.text
    assert http.get(endpoint,params={"cursor":page["nextCursor"]}).status_code==422
    intake=session(http,d,d["intake"])
    assert http.get(endpoint).status_code==403
    assert http.post(endpoint,headers=intake,json=payload).status_code==403
    manager=session(http,d,d["manager"])
    assert http.post(endpoint,headers=manager,json=payload).status_code==403
    manager_payload={**payload,"expectedActorId":str(d["manager"])}
    assert http.post(endpoint,headers=manager,json=manager_payload).status_code==409
    next_record=http.post(endpoint,headers=manager,json={**manager_payload,"requestKey":str(uuid4())})
    assert next_record.status_code==201,next_record.text
    assert next_record.json()["item"]["ownerId"]==str(d["manager"])
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT owner_id FROM auto_crm_interaction WHERE id=%s",(original["interactionId"],)).fetchone()==(d["intake"],)
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(endpoint).status_code==403
    assert http.post(endpoint,headers=manager,json={**manager_payload,"requestKey":str(uuid4())}).status_code==403


def test_deal_waits_for_reassignment_and_denies_former_intake(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic,sleep
    from roboport_api.auto_crm_deal import read_deal
    from roboport_api.auto_crm_leads import LeadDenied
    from roboport_api.auto_crm_workflow import assign_lead
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    intake=session(http,d,d["intake"])
    created=http.post(crm(org,loc)+"/leads",headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead_id=UUID(created.json()["leadId"])
    started=Event()
    backend=[]
    def former_owner():
        with psycopg.connect(DSN) as db:
            backend.append(db.info.backend_pid)
            started.set()
            with pytest.raises(LeadDenied):
                read_deal(db,d["tokens"][d["intake"]],org,loc,lead_id)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(DSN) as writer:
            with writer.transaction():
                assign_lead(writer,d["tokens"][d["manager"]],org,loc,lead_id,d["manager"],uuid4())
                future=pool.submit(former_owner)
                assert started.wait(timeout=5)
                # Prove the reader is waiting on the writer, not just sleeping.
                deadline=monotonic()+8
                with psycopg.connect(DSN,autocommit=True) as monitor:
                    while monotonic()<deadline:
                        blockers=monitor.execute("SELECT pg_blocking_pids(%s)",(backend[0],)).fetchone()[0]
                        if writer.info.backend_pid in blockers:
                            break
                        sleep(0.025)
                    else:
                        raise AssertionError("reader did not wait for assignment lock")
            future.result(timeout=10)
    manager=session(http,d,d["manager"])
    result=http.get(crm(org,loc)+"/leads/"+str(lead_id)+"/deal")
    assert result.status_code==200,result.text
    assert result.json()["leadId"]==str(lead_id)


def test_unified_timeline_full_contact_cycle_and_live_access(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=crm(org,loc)+"/leads"
    intake=session(http,d,d["intake"])
    created=http.post(base,headers=intake,json=lead_payload(d))
    assert created.status_code==201,created.text
    lead_id=created.json()["leadId"];detail=base+"/"+lead_id
    timeline=detail+"/timeline"
    due=datetime.now(timezone.utc)+timedelta(days=2)
    payload={"requestKey":str(uuid4()),"channelCode":"CALL","outcomeCode":"CALLBACK_REQUIRED",
             "callbackDueAt":due.isoformat(),"expectedActorId":str(d["intake"])}
    contact=http.post(detail+"/interactions",headers=intake,json=payload)
    assert contact.status_code==201,contact.text
    item=contact.json()["item"]
    assert http.post(detail+"/interactions",headers=intake,json=payload).json()["replayed"]
    qualified=http.post(detail+"/stages",headers=intake,json={"stageCode":"QUALIFIED","requestKey":str(uuid4())})
    assert qualified.status_code==201,qualified.text
    client=session(http,d,d["client_a"])
    consent="/api/v1/dev/account/crm-contact-consent"
    opted=http.post(consent,headers=client,json={"channelCode":"CALL","enabled":True,
        "expectedRevision":0,"requestKey":str(uuid4())})
    assert opted.status_code==201,opted.text
    assert http.get(timeline).status_code==403
    manager=session(http,d,d["manager"])
    mock_payload={"channelCode":"CALL","templateCode":"BOOKING_REMINDER","requestKey":str(uuid4())}
    mock=http.post(detail+"/mock-messages",headers=manager,json=mock_payload)
    assert mock.status_code==201,mock.text
    assert http.post(detail+"/mock-messages",headers=manager,json=mock_payload).json()["replayed"]
    assigned=http.post(detail+"/assignment",headers=manager,json={"assigneeId":str(d["manager"]),"requestKey":str(uuid4())})
    assert assigned.status_code==201,assigned.text
    with psycopg.connect(DSN) as db:
        from roboport_api.auto_crm_followups import resolve_followup
        resolve_followup(db,d["tokens"][d["manager"]],org,loc,UUID(lead_id),
                         UUID(item["callbackTaskId"]),uuid4(),"DONE")
    first=http.get(timeline)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    body=first.json()
    assert body["sendAllowed"] is False and body["legalConsentVerified"] is False
    by_kind={row["kind"]:row for row in body["items"]}
    assert set(by_kind)=={"CREATED","STAGE","ASSIGNED","INBOUND","TASK_CREATED","TASK_RESOLVED","MOCK"}
    assert len(body["items"])==7 and body["nextCursor"] is None
    assert by_kind["INBOUND"]["relatedId"]==by_kind["TASK_CREATED"]["eventId"]==by_kind["TASK_RESOLVED"]["relatedId"]
    assert by_kind["MOCK"]["status"]=="MOCK_ONLY"
    assert by_kind["ASSIGNED"]["relatedId"]==str(d["manager"])
    assert str(d["ca"]) not in first.text and "consentEventIds" not in first.text
    client=session(http,d,d["client_a"])
    assert http.post(consent,headers=client,json={"channelCode":"CALL","enabled":False,
        "expectedRevision":1,"requestKey":str(uuid4())}).status_code==201
    session(http,d,d["manager"])
    assert next(x for x in http.get(timeline).json()["items"] if x["kind"]=="MOCK")["status"]=="SUPPRESSED"
    # Same timestamp across several source tables is not a pagination collision.
    with psycopg.connect(DSN) as db:
        from roboport_api.auto_crm_interactions import record_interaction
        for _ in range(24):
            record_interaction(db,d["tokens"][d["manager"]],org,loc,UUID(lead_id),uuid4(),"EMAIL","INFORMATION_REQUESTED")
    page=http.get(timeline).json()
    assert len(page["items"])==25 and page["nextCursor"]
    later=http.get(timeline,params={"cursor":page["nextCursor"]})
    assert later.status_code==200,later.text
    rows=page["items"]+later.json()["items"]
    assert len(rows)==31 and len({(row["kind"],row["eventId"]) for row in rows})==31
    assert later.json()["nextCursor"] is None
    assert http.get(timeline,params={"cursor":"0."+page["nextCursor"].split(".",1)[1]}).status_code==422
    assert http.get(timeline.replace(str(loc),str(foreign_loc))).status_code==403
    session(http,d,d["intake"])
    assert http.get(timeline).status_code==403
    session(http,d,d["manager"])
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(timeline,params={"cursor":page["nextCursor"]}).status_code==403
