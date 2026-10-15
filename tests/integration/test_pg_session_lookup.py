"""Session validation depends on current PostgreSQL state, never client headers."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from roboport_api.session_lookup import session_subject, session_can_read_ticket
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_opaque_session_requires_active_user_link_and_fresh_expiry():
    user, customer, ticket, session = (uuid4() for _ in range(4))
    raw = new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,'session-subject')", (user,))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'session-owner')", (customer,))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (ticket,customer))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)", (user,customer))
        conn.execute(
            """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (session,user,token_digest(raw),token_digest(new_token()),
             now,now+timedelta(minutes=30),now+timedelta(hours=4)),
        )
        assert session_subject(conn,raw)==user
        assert session_can_read_ticket(conn,raw,ticket)
        assert session_subject(conn,new_token()) is None
        assert session_subject(conn,"") is None
        assert not session_can_read_ticket(conn,raw,uuid4())
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
        assert not session_can_read_ticket(conn,raw,ticket)
        conn.execute("UPDATE account_customer_link SET revoked_at=NULL WHERE user_id=%s",(user,))
        conn.execute("UPDATE app_user SET status='DISABLED' WHERE id=%s",(user,))
        assert session_subject(conn,raw) is None
        conn.execute("UPDATE app_user SET status='ACTIVE' WHERE id=%s",(user,))
        expired_token = new_token()
        conn.execute(
            """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uuid4(),user,token_digest(expired_token),token_digest(new_token()),
             now-timedelta(days=1),now-timedelta(hours=1),now+timedelta(hours=1)),
        )
        assert session_subject(conn,expired_token) is None
        conn.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(session,))
        assert session_subject(conn,raw) is None
