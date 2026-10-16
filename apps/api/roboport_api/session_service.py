"""Transactional opaque session issuance and revocation for local accounts.

Internal service only: do not expose until login rate limits, origin and CSRF gates exist.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg

from .session_tokens import new_token, token_digest


def issue_session(conn: psycopg.Connection, user_id: UUID) -> tuple[str, str] | None:
    with conn.transaction():
        valid = conn.execute(
            "SELECT 1 FROM app_user WHERE id=%s AND status='ACTIVE' FOR UPDATE",
            (user_id,),
        ).fetchone()
        if not valid:
            return None
        now = datetime.now(timezone.utc)
        raw, csrf = new_token(), new_token()
        conn.execute(
            """INSERT INTO user_session
               (id,user_id,token_hash,csrf_hash,created_at,last_seen_at,idle_expires_at,absolute_expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (uuid4(),user_id,token_digest(raw),token_digest(csrf),now,now,
             now+timedelta(minutes=30),now+timedelta(hours=12)),
        )
        return raw, csrf


def revoke_session(conn: psycopg.Connection, raw_token: str) -> bool:
    if not isinstance(raw_token, str) or len(raw_token) != 43:
        return False
    with conn.transaction():
        row = conn.execute(
            """UPDATE user_session SET revoked_at=now()
               WHERE token_hash=%s AND revoked_at IS NULL RETURNING id""",
            (token_digest(raw_token),),
        ).fetchone()
        return row is not None


def verify_csrf(conn: psycopg.Connection, raw_token: str, csrf: str) -> bool:
    if not isinstance(raw_token, str) or len(raw_token) != 43 or not isinstance(csrf, str) or len(csrf) != 43:
        return False
    row = conn.execute(
        """SELECT 1 FROM user_session s JOIN app_user u ON u.id=s.user_id
           LEFT JOIN user_session_csrf_rotation r ON r.session_id=s.id
           WHERE s.token_hash=%s AND COALESCE(r.csrf_hash,s.csrf_hash)=%s
           AND s.revoked_at IS NULL AND s.idle_expires_at>now()
           AND s.absolute_expires_at>now() AND u.status='ACTIVE'""",
        (token_digest(raw_token),token_digest(csrf)),
    ).fetchone()
    return row is not None
