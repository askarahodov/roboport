"""Ticket state endpoint: same-query ownership enforcement on real PostgreSQL."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_ticket_state_is_private_and_revocable(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,customer,ticket,other,session=(uuid4() for _ in range(5))
    raw=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(user,"state-"+user.hex))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'state-owner')",(customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)",(user,customer))
        conn.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (session,user,token_digest(raw),token_digest(new_token()),now,
             now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app)
    url=f"/api/v1/dev/tickets/{ticket}/state"
    assert client.get(url,headers={"X-User-ID":str(user)}).status_code==401
    client.cookies.set("rp_session",raw)
    res=client.get(url)
    assert res.status_code==200,res.text
    assert res.json()=={"id":str(ticket),"state":"DRAFT","version":1}
    assert res.headers["cache-control"]=="no-store"
    assert client.get(f"/api/v1/dev/tickets/{other}/state").status_code==404
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
    assert client.get(url).status_code==404
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(url).status_code==404
