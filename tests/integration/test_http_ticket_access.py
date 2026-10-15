"""Synthetic HTTP access probe: cookie-based subject only, never spoofed headers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_http_ticket_probe_denies_spoofing_and_revocations(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user_a,user_b,customer_a,customer_b,ticket_a,ticket_b,session=(uuid4() for _ in range(7))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        for uid,login in [(user_a,"probe-a"),(user_b,"probe-b")]:
            conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,login))
        for cid,label in [(customer_a,"probe a"),(customer_b,"probe b")]:
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(cid,label))
        for tid,cid in [(ticket_a,customer_a),(ticket_b,customer_b)]:
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(tid,cid))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(user_a,customer_a))
        conn.execute(
          """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
             VALUES (%s,%s,%s,%s,%s,%s,%s)""",
          (session,user_a,token_digest(token),token_digest(new_token()),
           now,now+timedelta(minutes=30),now+timedelta(hours=4)),
        )
    client=TestClient(app)
    address=f"/api/v1/dev/tickets/{ticket_a}/access"
    assert client.get(address,headers={"X-User-ID":str(user_a)}).status_code==401
    client.cookies.set("roboport_session",token)
    ok=client.get(address)
    assert ok.status_code==200 and ok.json()=={"id":str(ticket_a),"accessible":True}
    assert ok.headers["cache-control"]=="no-store"
    assert client.get(f"/api/v1/dev/tickets/{ticket_b}/access",headers={"X-User-ID":str(user_b)}).status_code==404
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user_a,))
    assert client.get(address).status_code==404
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(address).status_code==404
