"""Database ownership guard for use by future authenticated handlers.

The subject is supplied by verified session code only; never accept an HTTP header
as subject. Checks active user, active account/customer link and original claimant.
"""
from uuid import UUID
import psycopg


def may_read_ticket(conn: psycopg.Connection, subject_id: UUID, ticket_id: UUID) -> bool:
    row = conn.execute(
        """SELECT EXISTS(
             SELECT 1 FROM app_user u
             JOIN account_customer_link l ON l.user_id=u.id AND l.revoked_at IS NULL
             JOIN service_ticket t ON t.customer_id=l.customer_id
             WHERE u.id=%s AND u.status='ACTIVE' AND t.id=%s
           )""",
        (subject_id, ticket_id),
    ).fetchone()
    return bool(row and row[0])
