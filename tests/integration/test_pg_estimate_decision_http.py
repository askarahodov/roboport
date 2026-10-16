"""Real PG + HTTP owner-bound estimate consent; only synthetic identities."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_estimate_decision_http_replay_revocation_and_stale(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    customer, other_customer, ticket, old_quote, quote = (uuid4() for _ in range(5))
    owner, stranger = uuid4(), uuid4()
    tokens = [new_token(),new_token()]
    csrf = [new_token(),new_token()]
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for c in (customer,other_customer):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(c,c.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'AWAITING_APPROVAL')",(ticket,customer))
        for uid,token,secret in zip((owner,stranger),tokens,csrf):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (uid,"quote-owner-"+uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                       (uuid4(),uid))
            db.execute("""INSERT INTO user_session
                       (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),uid,token_digest(token),token_digest(secret),now,
                        now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (owner,customer))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (stranger,other_customer))
        for ident,version in ((old_quote,1),(quote,2)):
            db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
                       VALUES(%s,%s,%s,'SENT',15000,'RUB')""",(ident,ticket,version))
    http=TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/estimates/{quote}/decision"
    payload={"expectedVersion":2,"decision":"APPROVED"}
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf[0],
             "Idempotency-Key":"estimate-"+quote.hex}
    assert http.post(path,json=payload,headers=headers).status_code==401
    http.cookies.set("rp_session",tokens[1])
    assert http.post(path,json=payload,headers={**headers,"X-CSRF-Token":csrf[1]}).status_code==404
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[0])
    assert http.post(path,json=payload,headers={"Origin":"https://127.0.0.1","Idempotency-Key":"estimate-"+quote.hex}).status_code==403
    assert http.post(path,json=payload,headers={k:v for k,v in headers.items() if k!="Idempotency-Key"}).status_code==422
    assert http.post(f"/api/v1/dev/estimates/{old_quote}/decision",
                     json={"expectedVersion":1,"decision":"APPROVED"},
                     headers=headers).status_code==409
    ok=http.post(path,json=payload,headers=headers)
    assert ok.status_code==200,ok.text
    assert ok.json()=={"id":str(quote),"version":2,"state":"APPROVED"}
    replay=http.post(path,json=payload,headers=headers)
    assert replay.status_code==200 and replay.json()==ok.json()
    assert http.post(path,json={**payload,"decision":"DECLINED"},headers=headers).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM estimate_approval WHERE estimate_id=%s",
                          (quote,)).fetchone()[0]==1
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
                   (owner,))
    assert http.post(path,json=payload,headers=headers).status_code==404


def test_estimate_decline_is_immutable_and_requires_new_version():
    from roboport_api.estimate_decision import decide_estimate, EstimateConflict
    customer,ticket,quote,owner=(uuid4() for _ in range(4))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'DIAGNOSING')",(ticket,customer))
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (owner,"estimate-decline-"+owner.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),owner))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (owner,customer))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                   created_at,idle_expires_at,absolute_expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (uuid4(),owner,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
                   VALUES(%s,%s,1,'SENT',99,'RUB')""",(quote,ticket))
        result=decide_estimate(db,token,quote,1,"DECLINED","key-"+quote.hex)
        assert result["state"]=="DECLINED"
        assert decide_estimate(db,token,quote,1,"DECLINED","key-"+quote.hex)==result
        with pytest.raises(EstimateConflict):
            decide_estimate(db,token,quote,1,"APPROVED","different-"+quote.hex)
        assert db.execute("SELECT count(*) FROM estimate_approval WHERE estimate_id=%s",
                          (quote,)).fetchone()[0]==1
