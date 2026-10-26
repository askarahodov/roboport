"""Customer event timeline never reveals other owners or revoked bindings."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN

def test_live_customer_timeline_owner_isolation_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    a,b,ca,cb,ta,tb,sa,sb=(uuid4() for _ in range(8))
    tokens=[new_token(),new_token()]
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for uid in (a,b):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),uid))
        for cid in (ca,cb):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(cid,cid.hex))
        for ticket,cid in ((ta,ca),(tb,cb)):
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,cid))
            db.execute("""INSERT INTO ticket_state_event(id,ticket_id,from_state,to_state,expected_version)
                VALUES(%s,%s,'DRAFT','SUBMITTED',1)""",(uuid4(),ticket))
        for uid,cid in ((a,ca),(b,cb)):
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(uid,cid))
        for sid,uid,raw in ((sa,a,tokens[0]),(sb,b,tokens[1])):
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)""",(sid,uid,token_digest(raw),token_digest(new_token()),
                    now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    http=TestClient(app,base_url="https://127.0.0.1")
    uri=lambda t:f"/api/v1/dev/account/tickets/{t}/timeline"
    assert http.get(uri(ta)).status_code==401
    http.cookies.set("rp_session",tokens[0])
    own=http.get(uri(ta))
    assert own.status_code==200,own.text
    assert own.json()["items"][0]["toState"]=="SUBMITTED"
    assert "actor" not in str(own.json()).lower()
    assert http.get(uri(tb)).status_code==404
    http.cookies.clear();http.cookies.set("rp_session",tokens[1])
    assert http.get(uri(tb)).status_code==200
    assert http.get(uri(ta)).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(b,))
    assert http.get(uri(tb)).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s",(a,))
    http.cookies.clear();http.cookies.set("rp_session",tokens[0])
    assert http.get(uri(ta)).status_code==404
