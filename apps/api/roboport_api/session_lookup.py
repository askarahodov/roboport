"""Resolve sessions against live PostgreSQL; reject revoked, expired or disabled users.

No authentication HTTP endpoint is enabled by this module.
"""
from datetime import datetime, timezone
from uuid import UUID

import psycopg
from .session_tokens import token_digest


def session_subject(conn: psycopg.Connection, raw_token: str) -> UUID | None:
    if not isinstance(raw_token, str) or len(raw_token) != 43:
        return None
    digest = token_digest(raw_token)
    row = conn.execute(
        """SELECT s.user_id FROM user_session s
           JOIN app_user u ON u.id=s.user_id
           WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at > now() AND s.absolute_expires_at > now()
             AND u.status='ACTIVE'
        """, (digest,),
    ).fetchone()
    return row[0] if row else None


def session_can_read_ticket(conn: psycopg.Connection, token: str, ticket_id: UUID) -> bool:
    from .ownership import may_read_ticket

    subject = session_subject(conn, token)
    return subject is not None and may_read_ticket(conn, subject, ticket_id)
