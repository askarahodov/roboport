"""PG17/HTTPS: account-wide CRM choices, opt-out, BOLA, audit and concurrency."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_crm_contact_choices import change_customer_choice
from roboport_api.auto_crm_leads import LeadConflict
from test_pg_auto_crm_followups_http import setup,session
from test_pg_baseline import DSN

CLIENT_URL="/api/v1/dev/account/crm-contact-preferences"


def staff_url(org,loc,lead):
    return (f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
            f"/crm/leads/{lead}/contact-preferences")


def payload(code="CALL",enabled=True,version=0,key=None):
    return {"channelCode":code,"enabled":enabled,
            "expectedRevision":version,"requestKey":str(key or uuid4())}


def test_complete_customer_choice_to_staff_view_then_opt_out_and_revoke(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    owner=session(http,d,d["client_a"])
    initial=http.get(CLIENT_URL)
    assert initial.status_code==200,initial.text
    assert initial.headers["cache-control"]=="no-store"
    assert initial.json()=={
        "syntheticOnly":True,"sendAllowed":False,
        "channels":{"CALL":False,"EMAIL":False,"SMS":False},
        "revisions":{"CALL":0,"EMAIL":0,"SMS":0},
    }
    first=payload()
    assert http.post(CLIENT_URL,json=first).status_code==403
    yes=http.post(CLIENT_URL,headers=owner,json=first)
    assert yes.status_code==201,yes.text
    assert yes.json()["channels"]["CALL"] is True
    assert yes.json()["revisions"]["CALL"]==1
    assert yes.json()["sendAllowed"] is False
    replay=http.post(CLIENT_URL,headers=owner,json=first)
    assert replay.status_code==200 and replay.json()["replayed"] is True
    assert http.post(CLIENT_URL,headers=owner,json={
        **first,"enabled":False,
    }).status_code==409
    assert http.post(CLIENT_URL,headers=owner,json=payload()).status_code==409

    client_b=session(http,d,d["client_b"])
    assert http.get(CLIENT_URL).json()["channels"]["CALL"] is False
    assert http.post(CLIENT_URL,headers=client_b,json=first).status_code==409

    session(http,d,d["manager"])
    path=staff_url(org,loc,lead)
    staff=http.get(path)
    assert staff.status_code==200,staff.text
    assert staff.headers["cache-control"]=="no-store"
    assert staff.json()=={
        "syntheticOnly":True,"sendAllowed":False,
        "channels":{"CALL":True,"EMAIL":False,"SMS":False},
    }
    assert str(d["ca"]) not in staff.text
    assert str(d["client_a"]) not in staff.text
    assert http.get(staff_url(foreign,foreign_loc,lead)).status_code==403
    assert http.get(staff_url(org,loc,uuid4())).status_code==403
    assert http.get(staff_url(org,loc,"bad-uuid")).status_code==422

    # A second active client account linked to the same customer makes
    # the staff aggregate conservative until both explicitly opt in.
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (d["client_b"],d["ca"]))
    assert http.get(path).json()["channels"]["CALL"] is False
    b=session(http,d,d["client_b"])
    response=http.post(CLIENT_URL,headers=b,json=payload())
    assert response.status_code==201,response.text
    session(http,d,d["manager"])
    assert http.get(path).json()["channels"]["CALL"] is True

    session(http,d,d["client_a"])
    opt_out=payload(enabled=False,version=1)
    off=http.post(CLIENT_URL,headers=owner,json=opt_out)
    assert off.status_code==201,off.text
    assert off.json()["channels"]["CALL"] is False
    assert off.json()["revisions"]["CALL"]==2
    session(http,d,d["manager"])
    assert http.get(path).json()["channels"]["CALL"] is False
    # A revoked vehicle proof removes staff visibility, but cannot prevent
    # the customer from opting out of an account-wide contact preference.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(path).status_code==403
    session(http,d,d["client_a"])
    reselect=http.post(CLIENT_URL,headers=owner,json=payload("EMAIL",False))
    assert reselect.status_code==201,reselect.text
    assert http.get(CLIENT_URL).json()["channels"]["CALL"] is False

    with psycopg.connect(DSN) as db:
        rows=db.execute(
            """SELECT channel_code,enabled,version FROM auto_crm_contact_choice_event
               WHERE user_id=%s ORDER BY version,channel_code""",
            (d["client_a"],),
        ).fetchall()
        assert rows==[("CALL",True,1),("EMAIL",False,1),("CALL",False,2)] or sorted(rows)==sorted(
            [("CALL",True,1),("CALL",False,2),("EMAIL",False,1)]
        )
        assert db.execute(
            "SELECT count(*) FROM auto_crm_contact_choice_event WHERE user_id=%s",
            (d["client_a"],),
        ).fetchone()==(3,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_crm_contact_choice_event SET enabled=true WHERE user_id=%s",
                    (d["client_a"],),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_crm_contact_choice_event WHERE user_id=%s",
                    (d["client_a"],),
                )


def test_client_role_revoked_and_invalid_payload_fail_closed(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    client=session(http,d,d["client_a"])
    for invalid in [
        payload("WHATSAPP"),{**payload(),"enabled":"false"},
        {**payload(),"expectedRevision":True},
        {**payload(),"expectedRevision":-1},
        {**payload(),"requestKey":"not-a-uuid"},
        {**payload(),"unexpected":"field"},
    ]:
        assert http.post(CLIENT_URL,headers=client,json=invalid).status_code==422
    session(http,d,d["intake"])
    assert http.get(CLIENT_URL).status_code==403
    assert http.post(CLIENT_URL,headers=session(http,d,d["intake"]),
                     json=payload()).status_code==403
    session(http,d,d["client_a"])
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE user_role SET revoked_at=now()
               WHERE user_id=%s AND role_code='CLIENT'""",
            (d["client_a"],),
        )
    assert http.get(CLIENT_URL).status_code==403
    assert http.post(CLIENT_URL,headers=client,json=payload()).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_contact_choice_event WHERE user_id=%s",
            (d["client_a"],),
        ).fetchone()==(0,)


def test_same_key_parallel_replay_and_competing_choice_versions(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http,lead=setup(monkeypatch)
    token=d["tokens"][d["client_a"]]
    key=uuid4()
    barrier=Barrier(2)
    def same(_):
        barrier.wait(timeout=20)
        with psycopg.connect(DSN) as db:
            return change_customer_choice(db,token,"SMS",True,0,key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(same,range(2)))
    assert sorted(x["replayed"] for x in result)==[False,True]
    assert all(x["revisions"]["SMS"]==1 for x in result)
    barrier=Barrier(2)
    def compete(enabled):
        barrier.wait(timeout=20)
        try:
            with psycopg.connect(DSN) as db:
                return change_customer_choice(db,token,"SMS",enabled,1,uuid4())["replayed"]
        except LeadConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes=list(pool.map(compete,[False,True]))
    assert outcomes.count("CONFLICT")==1 and outcomes.count(False)==1
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_crm_contact_choice_event
               WHERE user_id=%s AND channel_code='SMS'""",
            (d["client_a"],),
        ).fetchone()==(2,)
        # DB refuses direct out-of-order version even with an active CLIENT.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_contact_choice_event
                       (id,user_id,channel_code,enabled,version,request_key)
                       VALUES(%s,%s,'SMS',false,7,%s)""",
                    (uuid4(),d["client_a"],uuid4()),
                )
