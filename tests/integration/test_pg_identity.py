"""Real PG checks for session identity, revocation and account/customer separation."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_identity_session_immutability_and_revocation():
    user, session = uuid4(), uuid4()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,'synthetic-login')",(user,))
        with pytest.raises(psycopg.errors.CheckViolation):
            with conn.transaction():
                conn.execute("INSERT INTO local_credential(user_id,password_hash) VALUES (%s,'plaintext')",(user,))
        conn.execute(
            """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,
               idle_expires_at,absolute_expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (session,user,"a"*64,"b"*64,now,now+timedelta(minutes=10),now+timedelta(days=1)),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE user_session SET token_hash=%s WHERE id=%s",("c"*64,session))
        conn.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(session,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE user_session SET revoked_at=NULL WHERE id=%s",(session,))


def test_customer_link_is_distinct_from_user_id_and_revoke_is_visible():
    user,customer=uuid4(),uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,'another-login')",(user,))
        conn.execute("INSERT INTO customer(id,label) VALUES (%s,'synthetic-client')",(customer,))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)",(user,customer))
        assert conn.execute("SELECT customer_id FROM account_customer_link WHERE user_id=%s AND revoked_at IS NULL",(user,)).fetchone()[0]==customer
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
        assert conn.execute("SELECT customer_id FROM account_customer_link WHERE user_id=%s AND revoked_at IS NULL",(user,)).fetchone() is None
