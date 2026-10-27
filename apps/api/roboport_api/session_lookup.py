"""Resolve sessions against live PostgreSQL; reject revoked, expired or disabled users.

No authentication HTTP endpoint is enabled by this module.
"""
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from .session_tokens import token_digest


def session_subject(
    conn: psycopg.Connection, raw_token: str, *, for_mutation: bool = False,
) -> UUID | None:
    """Resolve live session; optionally hold session/user grants until commit.

    Only callers inside a transactional business mutation set for_mutation.
    Read-only callers retain the previous non-locking query. FOR SHARE blocks
    concurrent session revocation or account deactivation until the mutation
    commits; revocation completed first makes the query return None.
    """
    if not isinstance(raw_token, str) or len(raw_token) != 43:
        return None
    digest = token_digest(raw_token)
    lock_clause = "FOR SHARE OF s,u" if for_mutation else ""
    row = conn.execute(
        f"""SELECT s.user_id FROM user_session s
           JOIN app_user u ON u.id=s.user_id
           WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at > now() AND s.absolute_expires_at > now()
             AND u.status='ACTIVE'
           {lock_clause}""",
        (digest,),
    ).fetchone()
    return row[0] if row else None


def session_can_read_ticket(conn: psycopg.Connection, token: str, ticket_id: UUID) -> bool:
    if not isinstance(token, str) or len(token) != 43:
        return False
    row = conn.execute(
        """SELECT EXISTS(
             SELECT 1 FROM user_session s
             JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
             JOIN account_customer_link l ON l.user_id=u.id AND l.revoked_at IS NULL
             JOIN service_ticket t ON t.customer_id=l.customer_id AND t.id=%s
             WHERE s.token_hash=%s AND s.revoked_at IS NULL
               AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
           )""",
        (ticket_id, token_digest(token)),
    ).fetchone()
    return bool(row and row[0])
