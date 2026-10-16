"""Contract-route and idempotency regression tests on actual PostgreSQL."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_contract_endpoints_staff_replay_and_account_key_scope(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    customer,ticket,other_ticket,manager,client=(uuid4() for _ in range(5))
    tm,tc=new_token(),new_token()
    cm,cc=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'DIAGNOSING')",(ticket,customer))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'COMPLETED')",(other_ticket,customer))
        for actor,role,token,csrf in ((manager,"MANAGER",tm,cm),(client,"CLIENT",tc,cc)):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(actor,actor.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",(uuid4(),actor,role))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                created_at,idle_expires_at,absolute_expires_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(),actor,token_digest(token),token_digest(csrf),now,
                 now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(client,customer))
        for target in (ticket,other_ticket):
            db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES (%s,%s,%s,'estimate.approve')",
                       (uuid4(),manager,target))
    http=TestClient(app,base_url="https://127.0.0.1")
    http.cookies.set("rp_session",tm)
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":cm,
             "Idempotency-Key":"create-"+ticket.hex}
    body={"currency":"RUB","expectedTicketVersion":1,
          "lines":[{"kind":"labor","description":"Diagnostics","quantity":2,"unitPrice":"12.50"}]}
    path=f"/api/v1/staff/tickets/{ticket}/estimates"
    assert http.post(path,json={**body,"lines":[{"kind":"part","description":"Unknown part","quantity":1,"unitPrice":"12.50","partId":str(uuid4())}]},headers=headers).status_code==422
    assert http.post(path,json={**body,"lines":[{"kind":"labor","quantity":1,"unitPrice":"12.50"}]},headers=headers).status_code==422
    assert http.post(path,json={**body,"lines":[{"kind":"labor","description":"X","quantity":0,"unitPrice":"12.50"}]},headers=headers).status_code==422
    assert http.post(path,json={**body,"lines":[{"kind":"labor","description":"X","quantity":1,"unitPrice":"12.500"}]},headers=headers).status_code==422
    assert http.post(path,json={**body,"expectedTicketVersion":2},headers=headers).status_code==409
    created=http.post(path,json=body,headers=headers)
    assert created.status_code==201,created.text
    assert created.json()["total"]=="25.00"
    assert created.json()["status"]=="DRAFT"
    eid=created.json()["id"]
    replay=http.post(path,json=body,headers=headers)
    assert replay.status_code==201 and replay.json()==created.json()
    assert http.post(path,json={**body,"lines":[{**body["lines"][0],"unitPrice":"13.50"}]},
                     headers=headers).status_code==409
    assert http.post(f"/api/v1/staff/tickets/{other_ticket}/estimates",json=body,
                     headers=headers).status_code==409
    send_headers={**headers,"Idempotency-Key":"send-"+eid}
    send_path=f"/api/v1/staff/estimates/{eid}/send"
    sent=http.post(send_path,headers=send_headers)
    assert sent.status_code==200,sent.text
    assert sent.json()["status"]=="SENT"
    second=http.post(send_path,headers=send_headers)
    assert second.status_code==200 and second.json()==sent.json()
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT state FROM service_ticket WHERE id=%s",(ticket,)).fetchone()[0]=="AWAITING_APPROVAL"
        assert db.execute("SELECT count(*) FROM ticket_state_event WHERE ticket_id=%s",(ticket,)).fetchone()[0]==2
        assert db.execute("SELECT count(*) FROM estimate WHERE ticket_id=%s",(ticket,)).fetchone()[0]==1
    http.cookies.clear()
    http.cookies.set("rp_session",tc)
    consent_headers={"Origin":"https://127.0.0.1","X-CSRF-Token":cc,
                     "Idempotency-Key":"approve-"+eid}
    consent_path=f"/api/v1/customer/estimates/{eid}/approve"
    consent=http.post(consent_path,json={"estimateVersion":1,"approvalMethod":"verified_account"},
                      headers=consent_headers)
    assert consent.status_code==200,consent.text
    assert consent.json()["status"]=="APPROVED"
    assert consent.json()["total"]=="25.00"
    assert http.post(consent_path,json={"estimateVersion":1,"approvalMethod":"verified_account"},
                     headers=consent_headers).status_code==200
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT state FROM service_ticket WHERE id=%s",(ticket,)).fetchone()[0]=="APPROVED"
        assert db.execute("SELECT count(*) FROM estimate_approval WHERE estimate_id=%s",(eid,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM ticket_state_event WHERE ticket_id=%s",(ticket,)).fetchone()[0]==3
        evidence=db.execute("SELECT consent_method,consent_fingerprint FROM estimate_approval WHERE estimate_id=%s",(eid,)).fetchone()
        assert evidence[0]=="verified_account" and len(evidence[1])==64


def test_decision_key_shared_across_two_estimates_of_same_account():
    from roboport_api.estimate_decision import decide_estimate, EstimateConflict
    customer,t1,t2,q1,q2,owner=(uuid4() for _ in range(6))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        for ticket in (t1,t2):
            db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'AWAITING_APPROVAL')",(ticket,customer))
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(owner,owner.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),owner))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(owner,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                    created_at,idle_expires_at,absolute_expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),owner,token_digest(token),token_digest(new_token()),now,
                     now+timedelta(minutes=30),now+timedelta(hours=4)))
        for q,ticket in ((q1,t1),(q2,t2)):
            db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
                          VALUES (%s,%s,1,'SENT',100,'RUB')""",(q,ticket))
        decide_estimate(db,token,q1,1,"APPROVED","same-actor-key")
        import pytest
        with pytest.raises(EstimateConflict):
            decide_estimate(db,token,q2,1,"APPROVED","same-actor-key")
        assert db.execute("SELECT count(*) FROM estimate_approval WHERE estimate_id=%s",(q2,)).fetchone()[0]==0
