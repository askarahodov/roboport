"""Real PostgreSQL tests for transactional session lifecycle."""
from uuid import uuid4

import psycopg

from roboport_api.session_lookup import session_subject
from roboport_api.session_service import issue_session, revoke_session, verify_csrf
from test_pg_baseline import DSN


def test_session_issue_csrf_revoke_and_no_replay():
    user = uuid4()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,'issue-user')",(user,))
        pair=issue_session(conn,user)
        assert pair is not None
        raw,csrf=pair
        assert session_subject(conn,raw)==user
        assert verify_csrf(conn,raw,csrf)
        assert not verify_csrf(conn,raw,"bad")
        assert not verify_csrf(conn,raw,raw)
        assert revoke_session(conn,raw)
        assert not revoke_session(conn,raw)
        assert session_subject(conn,raw) is None
        assert not verify_csrf(conn,raw,csrf)


def test_disabled_account_cannot_issue_session():
    user = uuid4()
    with psycopg.connect(DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login,status) VALUES (%s,'disabled-issuer','DISABLED')",(user,))
        assert issue_session(conn,user) is None
        assert issue_session(conn,uuid4()) is None
