"""PG17/HTTPS: staff CRM inbox, role scope, proof revocation and open-only ordering."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg

from test_pg_auto_crm_followups_http import setup, session, scope
from test_pg_baseline import DSN


def inbox(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/inbox"


def task_payload(days):
    return {
        "taskCode":"CALL_BACK",
        "dueAt":(datetime.now(timezone.utc)+timedelta(days=days)).isoformat(),
        "requestKey":str(uuid4()),
    }


def test_inbox_is_sorted_open_only_and_role_scoped(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    intake=session(http,d,d["intake"])
    first=http.post(scope(org,loc,lead),headers=intake,json=task_payload(9))
    assert first.status_code==201,first.text
    task=first.json()["taskId"]

    manager=session(http,d,d["manager"])
    created=http.post(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads",
        headers=manager,json={
            "customerLinkId":str(d["link"]), "sourceCode":"PHONE",
            "requestKey":str(uuid4()),
        })
    assert created.status_code==201,created.text
    other=created.json()["leadId"]
    second=http.post(scope(org,loc,other),headers=manager,json=task_payload(2))
    assert second.status_code==201,second.text
    other_task=second.json()["taskId"]

    response=http.get(inbox(org,loc))
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.json()["syntheticOnly"] is True
    assert [(x["leadId"],x["taskId"]) for x in response.json()["items"]]==[
        (other,other_task),(lead,task),
    ]
    assert all(x["status"]=="OPEN" and x["event"] is None
               for x in response.json()["items"])
    assert str(d["vehicle"]) not in response.text
    assert http.get(inbox(foreign,foreign_loc)).status_code==403
    assert http.get(inbox("not-a-uuid",loc)).status_code==422

    session(http,d,d["intake"])
    own=http.get(inbox(org,loc))
    assert own.status_code==200
    assert [x["leadId"] for x in own.json()["items"]]==[lead]
    resolved=http.post(scope(org,loc,lead)+"/"+task+"/events",headers=intake,
        json={"action":"DONE","requestKey":str(uuid4())})
    assert resolved.status_code==201,resolved.text
    assert http.get(inbox(org,loc)).json()["items"]==[]

    session(http,d,d["client_a"])
    assert http.get(inbox(org,loc)).status_code==403
    session(http,d,d["manager"])
    assert [x["taskId"] for x in http.get(inbox(org,loc)).json()["items"]]==[other_task]

    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(inbox(org,loc)).json()["items"]==[]


def test_inbox_recheck_after_manager_role_revoked(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    manager=session(http,d,d["manager"])
    assert http.get(inbox(org,loc)).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE user_id=%s AND location_id=%s AND role_code='MANAGER'""",
                   (d["manager"],loc))
    assert http.get(inbox(org,loc)).status_code==403


def test_inbox_ignores_task_resolved_between_query_and_serialization(monkeypatch):
    """READ COMMITTED race: event is committed after SELECT, before _task."""
    from contextlib import nullcontext
    from roboport_api import auto_crm_inbox

    lead_id,task_id,org,loc=uuid4(),uuid4(),uuid4(),uuid4()
    class Cursor:
        def fetchall(self):
            return [(lead_id,task_id)]
    class Connection:
        def transaction(self):
            return nullcontext()
        def execute(self,sql,params):
            assert "NOT EXISTS" in sql
            return Cursor()
    monkeypatch.setattr(auto_crm_inbox,"_authority",lambda *args:(uuid4(),True))
    monkeypatch.setattr(auto_crm_inbox,"_lead",lambda *args:None)
    monkeypatch.setattr(auto_crm_inbox,"_task",lambda *args:{"status":"DONE"})
    assert auto_crm_inbox.list_inbox(Connection(),"test",org,loc)=={
        "syntheticOnly":True,"items":[],
    }
