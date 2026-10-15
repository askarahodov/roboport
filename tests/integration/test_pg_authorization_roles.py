"""Один PostgreSQL-набор проверок роли, делегирования, отзыва и состояний."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from roboport_api.authorization import authorize_ticket, PERMISSIONS
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_live_role_acl_and_history_bundle():
    users = [uuid4(), uuid4(), uuid4()]
    customers = [uuid4(), uuid4()]
    tickets = [uuid4(), uuid4(), uuid4()]
    sessions = [uuid4(), uuid4(), uuid4()]
    tokens = [new_token() for _ in users]
    grants = [uuid4() for _ in users]
    acl = uuid4()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        for i, user in enumerate(users):
            conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                         (user, "role-"+user.hex))
            conn.execute(
                """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (sessions[i],user,token_digest(tokens[i]),token_digest(new_token()),
                 now,now+timedelta(minutes=30),now+timedelta(hours=4)))
        for customer in customers:
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                         (customer,customer.hex))
        for i, ticket in enumerate(tickets):
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                         (ticket,customers[0] if i < 2 else customers[1]))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                     (users[0],customers[0]))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                     (users[1],customers[1]))
        for i, role in enumerate(("CLIENT","CLIENT","ENGINEER")):
            conn.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                         (grants[i],users[i],role))

    with psycopg.connect(DSN) as conn:
        # 1–4: клиент видит только заявки того customer, которому выдана связь.
        assert authorize_ticket(conn,tokens[0],"ticket.read",tickets[0])
        assert authorize_ticket(conn,tokens[0],"ticket.read",tickets[1])
        assert not authorize_ticket(conn,tokens[0],"ticket.read",tickets[2])
        assert not authorize_ticket(conn,tokens[1],"ticket.read",tickets[0])
        # 5: роль ENGINEER без назначения не даёт доступа.
        assert not authorize_ticket(conn,tokens[2],"ticket.read",tickets[0])
        conn.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
                        VALUES(%s,%s,%s,'ticket.diagnose')""",
                     (acl,users[2],tickets[0]))
        # 6–7: назначение позволяет диагностику, но не чтение без отдельного ACL.
        assert authorize_ticket(conn,tokens[2],"ticket.diagnose",tickets[0])
        assert not authorize_ticket(conn,tokens[2],"ticket.read",tickets[0])
        # 8: состояние SAFETY_HOLD блокирует диагностику.
        conn.execute("UPDATE service_ticket SET state='SAFETY_HOLD' WHERE id=%s",(tickets[0],))
        assert not authorize_ticket(conn,tokens[2],"ticket.diagnose",tickets[0])
        # 9: отзыв назначения немедленно блокирует действие.
        conn.execute("UPDATE service_ticket SET state='DRAFT' WHERE id=%s",(tickets[0],))
        conn.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
        assert not authorize_ticket(conn,tokens[2],"ticket.diagnose",tickets[0])
        # 10: нельзя отменить отзыв ACL.
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE acl_entry SET revoked_at=NULL WHERE id=%s",(acl,))
        # 11: отзыв роли CLIENT блокирует доступ при активной сессии.
        conn.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(grants[0],))
        assert not authorize_ticket(conn,tokens[0],"ticket.read",tickets[1])
        # 12: ADMIN не получает PII по одному лишь названию роли.
        conn.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'ADMIN')",
                     (uuid4(),users[2]))
        assert not authorize_ticket(conn,tokens[2],"ticket.read",tickets[2])
        # 13: неизвестное действие — deny by default.
        assert not authorize_ticket(conn,tokens[1],"ticket.delete",tickets[2])
        # 14: отзыв сессии немедленно блокирует доступ клиента B.
        conn.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(sessions[1],))
        assert not authorize_ticket(conn,tokens[1],"ticket.read",tickets[2])


def test_permission_matrix_defaults():
    assert len(PERMISSIONS) == 9
    assert not PERMISSIONS["ADMIN"]
    assert not PERMISSIONS["PUBLIC"]
    assert "ticket.read" not in PERMISSIONS["STOCK"]


def test_audited_http_permission_and_revoke_bundle(monkeypatch):
    """Пакет HTTP проверки RBAC, отзыва grant и аудита решений."""
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    user, customer, ticket, session, grant = (uuid4() for _ in range(5))
    raw = new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                     (user, "http-role-"+user.hex))
        conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)", (customer,customer.hex))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                     (ticket,customer))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                     (user,customer))
        conn.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                     (session,user,token_digest(raw),token_digest(new_token()),now,
                      now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/authorized/tickets/{ticket}"
    assert client.get(url).status_code==401
    client.cookies.set("rp_session",raw)
    # No role -> 404 and a durable denial audit.
    assert client.get(url).status_code==404
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                     (grant,user))
    assert client.get(url).status_code==200
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(grant,))
    assert client.get(url).status_code==404
    with psycopg.connect(DSN) as conn:
        outcomes=[r[0] for r in conn.execute(
            "SELECT outcome FROM authorization_audit WHERE actor_id=%s ORDER BY created_at",
            (user,)).fetchall()]
    assert outcomes==["DENY","ALLOW","DENY"]
    assert client.get(url,headers={"X-User-ID":str(user)}).status_code==404
    # A replay hint must never bypass authorization after revocation.
    assert client.get(url,headers={"Idempotency-Key":"replay-synthetic-1"}).status_code==404
    with psycopg.connect(DSN) as conn:
        audit_id=conn.execute(
            "SELECT id FROM authorization_audit WHERE actor_id=%s LIMIT 1",
            (user,)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE authorization_audit SET outcome='ALLOW' WHERE id=%s",
                             (audit_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("DELETE FROM authorization_audit WHERE id=%s",
                             (audit_id,))
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(url).status_code==404
