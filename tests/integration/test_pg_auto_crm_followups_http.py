"""PG17+HTTPS: synthetic CRM manual follow-up tasks, no outbound contact."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from zoneinfo import ZoneInfo

from roboport_api.main import app
from roboport_api.auto_crm_leads import create_lead
from roboport_api.auto_crm_followups import create_followup
from test_pg_auto_bay_occupancy_http import _fixture
from test_pg_auto_crm_leads_http import session
from test_pg_baseline import DSN


def base_url(org,loc,lead):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/followups"


def setup(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        created=create_lead(db,d["tokens"][d["intake"]],org,loc,
                            d["link"],"WEB",uuid4())
        lead=created["leadId"]
        tz=db.execute(
            "SELECT timezone_name FROM auto_service_location WHERE id=%s",
            (loc,),
        ).fetchone()[0]
    due=(datetime.now(timezone.utc).astimezone(ZoneInfo(tz)).date()
         +timedelta(days=9)).isoformat()
    return d,org,loc,lead,foreign,foreign_loc,http,due


def test_followup_full_create_done_cancel_replay_and_revocation(monkeypatch):
    d,org,loc,lead,foreign,foreign_loc,http,due=setup(monkeypatch)
    url=base_url(org,loc,lead)
    assert http.get(url).status_code==403
    create={"kind":"CALLBACK_NOT_SENT","dueDay":due,"requestKey":str(uuid4())}
    assert http.post(url,json=create).status_code==403
    headers=session(http,d,d["intake"])
    assert http.post(url,json=create).status_code==403
    empty=http.get(url)
    assert empty.status_code==200 and empty.json()=={"syntheticOnly":True,"items":[]}
    done=http.post(url,json=create,headers=headers)
    assert done.status_code==201,done.text
    item=done.json()["task"]
    ident=item["id"]
    assert item["status"]=="OPEN" and item["reasonCode"] is None
    assert item["ownerId"]==str(d["intake"])
    assert item["dueDay"]==due
    assert done.headers["cache-control"]=="no-store"
    repeated=http.post(url,json=create,headers=headers)
    assert repeated.status_code==200 and repeated.json()["replayed"] is True
    assert repeated.json()["task"]["id"]==ident
    assert http.post(url,json={**create,"requestKey":str(uuid4())},
                     headers=headers).status_code==409
    assert http.post(url,json={**create,"dueDay":(datetime.now(timezone.utc).date()-
        timedelta(days=3)).isoformat(),"requestKey":str(uuid4())},
        headers=headers).status_code==422
    assert http.post(url,json={**create,"kind":"BAD","requestKey":str(uuid4())},
                     headers=headers).status_code==422
    target=url+"/"+ident+"/finish"
    closed={"requestKey":str(uuid4()),"action":"DONE","reasonCode":None}
    assert http.post(target,json=closed).status_code==403
    completed=http.post(target,json=closed,headers=headers)
    assert completed.status_code==201,completed.text
    assert completed.json()["task"]["status"]=="DONE"
    assert http.post(target,json=closed,headers=headers).status_code==200
    assert http.post(target,json={**closed,"requestKey":str(uuid4())},
                     headers=headers).status_code==409
    assert http.post(target,json={"requestKey":str(uuid4()),"action":"CANCELED",
        "reasonCode":"OTHER"},headers=headers).status_code==409
    second=http.post(url,json={"kind":"CHECK_DOCUMENTS","dueDay":due,
        "requestKey":str(uuid4())},headers=headers)
    assert second.status_code==201,second.text
    sid=second.json()["task"]["id"]
    assert http.post(url+"/"+sid+"/finish",json={"requestKey":str(uuid4()),
        "action":"CANCELED","reasonCode":None},headers=headers).status_code==422
    canceled=http.post(url+"/"+sid+"/finish",json={"requestKey":str(uuid4()),
        "action":"CANCELED","reasonCode":"NO_LONGER_NEEDED"},headers=headers)
    assert canceled.status_code==201,canceled.text
    assert canceled.json()["task"]["status"]=="CANCELED"
    assert canceled.json()["task"]["reasonCode"]=="NO_LONGER_NEEDED"
    listed=http.get(url)
    assert listed.status_code==200 and len(listed.json()["items"])==2
    assert str(d["vehicle"]) not in listed.text and str(d["link"]) not in listed.text
    assert "phone" not in listed.text.lower()
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_followup_task WHERE lead_id=%s",
            (lead,),
        ).fetchone()[0]==2
        assert db.execute(
            """SELECT count(*) FROM auto_crm_followup_event e
               JOIN auto_crm_followup_task t ON t.id=e.task_id
               WHERE t.lead_id=%s""",
            (lead,),
        ).fetchone()[0]==2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_crm_followup_task SET due_on=now()::date WHERE id=%s",
                           (ident,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_crm_followup_event WHERE task_id=%s",
                           (sid,))
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='INTAKE'""",
            (org,loc,d["intake"]),
        )
    assert http.get(url).status_code==403
    assert http.post(target,json=closed,headers=headers).status_code==403
    assert http.get(base_url(foreign,foreign_loc,lead)).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert http.get(url).status_code==404


def test_manager_access_and_live_owner_proof(monkeypatch):
    d,org,loc,lead,foreign,foreign_loc,http,due=setup(monkeypatch)
    headers=session(http,d,d["manager"])
    url=base_url(org,loc,lead)
    assert http.get(url).status_code==200
    data={"kind":"CONFIRM_BOOKING","dueDay":due,"requestKey":str(uuid4())}
    created=http.post(url,json=data,headers=headers)
    assert created.status_code==201,created.text
    target=url+"/"+created.json()["task"]["id"]+"/finish"
    response=http.post(target,json={"action":"DONE",
        "requestKey":str(uuid4())},headers=headers)
    assert response.status_code==201,response.text
    # Revoking the original verified link invalidates both manager and owner.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(url).status_code==403
    assert http.post(url,json=data,headers=headers).status_code==403


def test_concurrent_exact_key_creates_one_crm_followup(monkeypatch):
    d,org,loc,lead,foreign,foreign_loc,http,due=setup(monkeypatch)
    key=uuid4()
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=15)
        with psycopg.connect(DSN) as db:
            result=create_followup(db,d["tokens"][d["intake"]],org,loc,UUID(lead),request_key=key,
                kind="CHECK_DOCUMENTS",due_day=due)
            return result["replayed"],result["task"]["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,range(2)))
    assert sorted(x[0] for x in results)==[False,True]
    assert results[0][1]==results[1][1]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_followup_task WHERE lead_id=%s",
            (lead,),
        ).fetchone()[0]==1
