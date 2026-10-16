"""Real PG FSM transition, version and immutable event regression."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.session_tokens import new_token, token_digest
from roboport_api.ticket_fsm import transition_ticket, TransitionConflict
from test_pg_baseline import DSN


def test_staff_intake_transition_version_audit_and_safety_bundle():
    user, customer, ticket, session, role, acl = (uuid4() for _ in range(6))
    token = new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.intake')",
                   (acl,user,ticket))
    with psycopg.connect(DSN) as db:
        # 1 legal change; 2 event actor; 3 optimistic version; 4 immutable audit
        result=transition_ticket(db,token,ticket,"SUBMITTED",1)
        assert result["version"]==2 and result["state"]=="SUBMITTED"
        events=db.execute("""SELECT from_state,to_state,expected_version,actor_id FROM ticket_state_event
                              WHERE ticket_id=%s""",(ticket,)).fetchall()
        assert events==[("DRAFT","SUBMITTED",1,user)]
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"TRIAGE",1)
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"COMPLETED",2)
        # 5 next legal transition; 6 revoke immediately blocks mutation.
        assert transition_ticket(db,token,ticket,"TRIAGE",2)["version"]==3
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"AWAITING_DELIVERY",3)
        assert db.execute("SELECT state,version FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()==("TRIAGE",3)
        event_id=db.execute("SELECT id FROM ticket_state_event WHERE ticket_id=%s LIMIT 1",
                            (ticket,)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM ticket_state_event WHERE id=%s",(event_id,))
        # 7 Anonymous token denied; 8 QC bypass denied by graph.
        with pytest.raises(TransitionConflict):
            transition_ticket(db,new_token(),ticket,"AWAITING_DELIVERY",3)


def test_safety_and_qc_guard_deny_illegal_shortcuts():
    user,customer,ticket,session,role,acl=(uuid4() for _ in range(6))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-engineer-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'SAFETY_HOLD')",
                   (ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'ENGINEER')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.diagnose')",
                   (acl,user,ticket))
    with psycopg.connect(DSN) as db:
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"REPAIRING",1)
        db.execute("UPDATE service_ticket SET state='QC_FAILED' WHERE id=%s",(ticket,))
        with pytest.raises(TransitionConflict):
            transition_ticket(db,token,ticket,"READY_FOR_HANDOVER",1)
        assert transition_ticket(db,token,ticket,"REPAIRING",1)["version"]==2

 
def test_http_fsm_csrf_origin_version_and_revocation_bundle(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,customer,ticket,session,role,acl=(uuid4() for _ in range(6))
    token,csrf=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-http-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(csrf),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.intake')",
                   (acl,user,ticket))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/tickets/{ticket}/transition"
    payload={"toState":"SUBMITTED","expectedVersion":1}
    assert client.post(url,json=payload).status_code==401
    client.cookies.set("rp_session",token)
    assert client.post(url,json=payload).status_code==403
    assert client.post(url,json=payload,headers={"Origin":"https://127.0.0.1"}).status_code==403
    hdr={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf,"Idempotency-Key":"fsm-"+ticket.hex}
    ok=client.post(url,json=payload,headers=hdr)
    assert ok.status_code==200,ok.text
    assert ok.json()["version"]==2
    replay=client.post(url,json=payload,headers=hdr)
    assert replay.status_code==200 and replay.json()==ok.json()
    assert client.post(url,json={"toState":"COMPLETED","expectedVersion":2},headers=hdr).status_code==409
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
    assert client.post(url,json=payload,headers=hdr).status_code==409
    assert client.post(url,json={"toState":"TRIAGE","expectedVersion":2},headers=hdr).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT state,version FROM service_ticket WHERE id=%s",(ticket,)).fetchone()==("SUBMITTED",2)
        assert db.execute("SELECT count(*) FROM ticket_state_event WHERE ticket_id=%s",(ticket,)).fetchone()[0]==1


def test_concurrent_version_conflict_does_not_duplicate_events():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    user,customer,ticket,session,role,acl=(uuid4() for _ in range(6))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"fsm-concurrent-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (role,user))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.intake')",
                   (acl,user,ticket))
    barrier=Barrier(2)
    def attempt():
        with psycopg.connect(DSN) as conn:
            barrier.wait(timeout=10)
            try:
                return transition_ticket(conn,token,ticket,"SUBMITTED",1)["version"]
            except TransitionConflict:
                return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:attempt(),range(2)))
    assert sorted(map(str,results))==["2","CONFLICT"]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT state,version FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()==("SUBMITTED",2)
        assert db.execute("SELECT count(*) FROM ticket_state_event WHERE ticket_id=%s",
                          (ticket,)).fetchone()[0]==1


def test_independent_safety_expert_release_and_revoke():
    from roboport_api.ticket_fsm import record_safety_review, release_safety_hold
    users=[uuid4(),uuid4()]
    tokens=[new_token(),new_token()]
    ticket,customer=uuid4(),uuid4()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'SAFETY_HOLD')",
                   (ticket,customer))
        for user,token,role,action in zip(users,tokens,("ENGINEER","MANAGER"),
                                          ("ticket.diagnose","ticket.manage")):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (user,"safety-"+user.hex))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),user,token_digest(token),token_digest(new_token()),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(),user,role))
            db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,%s)",
                       (uuid4(),user,ticket,action))
    with psycopg.connect(DSN) as db:
        with pytest.raises(TransitionConflict):
            release_safety_hold(db,tokens[1],ticket,1)
        assert record_safety_review(db,tokens[0],ticket,1,True)=="ENGINEER"
        with pytest.raises(TransitionConflict):
            release_safety_hold(db,tokens[1],ticket,1)
        assert record_safety_review(db,tokens[1],ticket,1,True)=="MANAGER"
        # Disabled reviewers cannot authorize release even after approval.
        db.execute("UPDATE app_user SET status='DISABLED' WHERE id=%s",(users[0],))
        with pytest.raises(TransitionConflict):
            release_safety_hold(db,tokens[1],ticket,1)
        db.execute("UPDATE app_user SET status='ACTIVE' WHERE id=%s",(users[0],))
        # Manager privilege takes precedence over an additional engineer grant.
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'ENGINEER')",
                   (uuid4(),users[1]))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.diagnose')",
                   (uuid4(),users[1],ticket))
        assert release_safety_hold(db,tokens[1],ticket,1)["version"]==2
        assert db.execute("SELECT state,version FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()==("DIAGNOSING",2)
        with pytest.raises(TransitionConflict):
            release_safety_hold(db,tokens[1],ticket,1)
        assert db.execute("SELECT count(*) FROM ticket_state_event WHERE ticket_id=%s",
                          (ticket,)).fetchone()[0]==1


def test_http_safety_review_requires_two_independent_staff():
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        patch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
        patch.setenv("ROBOport_DATABASE_URL",DSN)
        users=[uuid4(),uuid4()]
        tokens=[new_token(),new_token()]
        csrf=[new_token(),new_token()]
        ticket,customer=uuid4(),uuid4()
        now=datetime.now(timezone.utc)
        with psycopg.connect(DSN) as db:
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
            db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'SAFETY_HOLD')",
                       (ticket,customer))
            for i,(user,role,action) in enumerate(zip(users,("ENGINEER","MANAGER"),
                                                       ("ticket.diagnose","ticket.manage"))):
                db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                           (user,"safety-http-"+user.hex))
                db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                              VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                           (uuid4(),user,token_digest(tokens[i]),token_digest(csrf[i]),now,
                            now+timedelta(minutes=30),now+timedelta(hours=4)))
                db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                           (uuid4(),user,role))
                db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,%s)",
                           (uuid4(),user,ticket,action))
        client=TestClient(app,base_url="https://127.0.0.1")
        review=f"/api/v1/dev/tickets/{ticket}/safety-review"
        release=f"/api/v1/dev/tickets/{ticket}/safety-release"
        payload={"expectedVersion":1}
        client.cookies.set("rp_session",tokens[1])
        headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[1],"Idempotency-Key":"release-"+ticket.hex}
        assert client.post(release,json=payload,headers=headers).status_code==409
        assert client.post(review,json={**payload,"approved":True},headers=headers).status_code==200
        assert client.post(release,json=payload,headers=headers).status_code==409
        client.cookies.clear()
        client.cookies.set("rp_session",tokens[0])
        eng_headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[0],"Idempotency-Key":"review-eng-"+ticket.hex}
        first_review=client.post(review,json={**payload,"approved":True},headers=eng_headers)
        assert first_review.status_code==200
        repeated_review=client.post(review,json={**payload,"approved":True},headers=eng_headers)
        assert repeated_review.status_code==200 and repeated_review.json()==first_review.json()
        assert client.post(review,json={**payload,"approved":False},headers=eng_headers).status_code==409
        with psycopg.connect(DSN) as db:
            assert db.execute("SELECT count(*) FROM ticket_safety_review WHERE ticket_id=%s AND actor_id=%s",
                              (ticket,users[0])).fetchone()[0]==1
        client.cookies.clear()
        client.cookies.set("rp_session",tokens[1])
        ok=client.post(release,json=payload,headers=headers)
        assert ok.status_code==200
        replay=client.post(release,json=payload,headers=headers)
        assert replay.status_code==200 and replay.json()==ok.json()
        assert client.post(release,json={"expectedVersion":2},headers=headers).status_code==409
