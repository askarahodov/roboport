"""End-to-end synthetic manager creation / send / owner approval."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_staff_draft_send_owner_agreement_and_immutable_price(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    cust,ticket,manager,owner=(uuid4() for _ in range(4))
    tokens=(new_token(),new_token())
    cs=(new_token(),new_token())
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(cust,cust.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,cust))
        for uid,role,token,csrf in zip((manager,owner),("MANAGER","CLIENT"),tokens,cs):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(),uid,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                created_at,idle_expires_at,absolute_expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(),uid,token_digest(token),token_digest(csrf),now,
                 now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (owner,cust))
        db.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
                      VALUES(%s,%s,%s,'estimate.approve')""",(uuid4(),manager,ticket))
    http=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/tickets/{ticket}/estimates"
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":cs[0]}
    http.cookies.set("rp_session",tokens[1])
    assert http.post(url,json={"lines":[{"description":"Тест","amountMinor":100}]},
                     headers={"Origin":"https://127.0.0.1","X-CSRF-Token":cs[1]}).status_code==404
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[0])
    draft=http.post(url,json={"lines":[{"description":"Работа","amountMinor":250},
                                         {"description":"Деталь","amountMinor":75}]},headers=headers)
    assert draft.status_code==201,draft.text
    assert draft.json()["totalMinor"]==325
    eid=draft.json()["id"]
    assert http.post(url,json={"lines":[{"description":"Ещё","amountMinor":1}]},
                     headers=headers).status_code==409
    send=http.post(f"/api/v1/dev/estimates/{eid}/send",
                   json={"expectedVersion":1},headers=headers)
    assert send.status_code==200,send.text
    assert send.json()["state"]=="SENT"
    assert http.post(f"/api/v1/dev/estimates/{eid}/send",
                     json={"expectedVersion":1},headers=headers).status_code==409
    with psycopg.connect(DSN) as db:
        import pytest
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE estimate SET total_minor=1 WHERE id=%s",(eid,))
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    consent=http.post(f"/api/v1/dev/estimates/{eid}/decision",
        json={"expectedVersion":1,"decision":"APPROVED"},
        headers={"Origin":"https://127.0.0.1","X-CSRF-Token":cs[1],
                 "Idempotency-Key":"consent-"+eid})
    assert consent.status_code==200,consent.text
    assert consent.json()["state"]=="APPROVED"


def test_price_change_forces_new_version_and_new_owner_consent(monkeypatch):
    from roboport_api.estimate_decision import create_estimate_version,send_estimate_version,decide_estimate,EstimateConflict
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    customer,ticket,manager,owner=(uuid4() for _ in range(4))
    now=datetime.now(timezone.utc)
    manager_token,owner_token=new_token(),new_token()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        for uid,role,tok in ((manager,"MANAGER",manager_token),(owner,"CLIENT",owner_token)):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",(uuid4(),uid,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                        created_at,idle_expires_at,absolute_expires_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),uid,token_digest(tok),token_digest(new_token()),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(owner,customer))
        db.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
                      VALUES(%s,%s,%s,'estimate.approve')""",(uuid4(),manager,ticket))
        old=create_estimate_version(db,manager_token,ticket,[{"description":"Работа","amountMinor":100}])
        send_estimate_version(db,manager_token,__import__("uuid").UUID(old["id"]),1)
        newer=create_estimate_version(db,manager_token,ticket,[{"description":"Работа","amountMinor":200}])
        assert newer["version"]==2
        send_estimate_version(db,manager_token,__import__("uuid").UUID(newer["id"]),2)
        import pytest
        with pytest.raises(EstimateConflict):
            decide_estimate(db,owner_token,__import__("uuid").UUID(old["id"]),1,"APPROVED","old")
        actual=decide_estimate(db,owner_token,__import__("uuid").UUID(newer["id"]),2,"APPROVED","new")
        assert actual["version"]==2 and actual["state"]=="APPROVED"
        assert db.execute("SELECT count(*) FROM estimate_approval WHERE estimate_id=%s",
                          (old["id"],)).fetchone()[0]==0
