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
    client=TestClient(app, base_url="https://127.0.0.1")
    address=f"/api/v1/dev/tickets/{ticket_a}/access"
    assert client.get(address,headers={"X-User-ID":str(user_a)}).status_code==401
    client.cookies.set("rp_session",token)
    ok=client.get(address)
    assert ok.status_code==200 and ok.json()=={"id":str(ticket_a),"accessible":True}
    assert ok.headers["cache-control"]=="no-store"
    assert client.get(f"/api/v1/dev/tickets/{ticket_b}/access",headers={"X-User-ID":str(user_b)}).status_code==404
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user_a,))
    assert client.get(address).status_code==404
    monkeypatch.setenv("ROBOport_DATABASE_URL","postgresql://127.0.0.1@remote.example/roboport")
    assert client.get(address).status_code==404
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(address).status_code==404


def test_object_access_security_bundle(monkeypatch):
    """Десять HTTP-проверок BOLA и немедленного отзыва на реальном PostgreSQL."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    users=[uuid4(),uuid4()]
    customers=[uuid4(),uuid4()]
    tickets=[uuid4(),uuid4()]
    sessions=[uuid4(),uuid4()]
    tokens=[new_token(),new_token()]
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        for i in range(2):
            conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                         (users[i],"object-"+users[i].hex))
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                         (customers[i],"object-"+customers[i].hex))
            conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                         (users[i],customers[i]))
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                         (tickets[i],customers[i]))
            conn.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                         (sessions[i],users[i],token_digest(tokens[i]),token_digest(new_token()),
                          now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app,base_url="https://127.0.0.1")
    paths=lambda ticket: (
        f"/api/v1/dev/tickets/{ticket}",
        f"/api/v1/dev/tickets/{ticket}/state",
        f"/api/v1/dev/tickets/{ticket}/access",
    )
    # 1: подделанный заголовок не аутентифицирует анонимного клиента.
    assert client.get(paths(tickets[0])[0],headers={"X-User-ID":str(users[0])}).status_code==401
    client.cookies.set("rp_session",tokens[0])
    # 2–4: все три HTTP-операции читают только свою заявку.
    for path in paths(tickets[0]):
        assert client.get(path).status_code==200
    # 5–7: UUID чужой заявки не помогает, даже с поддельным заголовком.
    for path in paths(tickets[1]):
        denied=client.get(path,headers={"X-Customer-ID":str(customers[1]),
                                        "X-User-ID":str(users[1])})
        assert denied.status_code==404
        assert denied.headers["cache-control"]=="no-store"
    # 8: после отзыва customer linkage все три операции закрыты.
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
                     (users[0],))
    for path in paths(tickets[0]):
        assert client.get(path).status_code==404
    # 9: сессия другого клиента и его заявка не затронуты.
    client.cookies.clear()
    client.cookies.set("rp_session",tokens[1])
    for path in paths(tickets[1]):
        assert client.get(path).status_code==200
    # 10: отзыв сессии немедленно закрывает все три HTTP-операции.
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(sessions[1],))
    for path in paths(tickets[1]):
        assert client.get(path).status_code==404
