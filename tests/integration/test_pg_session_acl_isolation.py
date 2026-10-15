"""Regression: a verified session never inherits another claimant's ticket."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg

from roboport_api.session_lookup import session_can_read_ticket
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_session_ticket_access_is_not_transferable():
    a, b, ca, cb, ta, tb, sa, sb = (uuid4() for _ in range(8))
    raw_a, raw_b = new_token(), new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        for uid, login in ((a, f"owner-{a.hex}"), (b, f"owner-{b.hex}")):
            conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)", (uid, login))
        for cid in (ca, cb):
            conn.execute("INSERT INTO customer(id,label) VALUES (%s,%s)", (cid, str(cid)))
        for tid, cid in ((ta, ca), (tb, cb)):
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (tid, cid))
        for uid, cid in ((a, ca), (b, cb)):
            conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)", (uid, cid))
        for sid, uid, raw in ((sa, a, raw_a), (sb, b, raw_b)):
            conn.execute(
                """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,
                       idle_expires_at,absolute_expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (sid, uid, token_digest(raw), token_digest(new_token()), now,
                 now + timedelta(minutes=30), now + timedelta(hours=4)),
            )
        assert session_can_read_ticket(conn, raw_a, ta)
        assert session_can_read_ticket(conn, raw_b, tb)
        assert not session_can_read_ticket(conn, raw_a, tb)
        assert not session_can_read_ticket(conn, raw_b, ta)
        assert not session_can_read_ticket(conn, raw_a, uuid4())
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s", (a,))
        assert not session_can_read_ticket(conn, raw_a, ta)
        assert session_can_read_ticket(conn, raw_b, tb)
        conn.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s", (sb,))
        assert not session_can_read_ticket(conn, raw_b, tb)
