"""Read minimal ticket state with authorization in the same SQL statement."""
from uuid import UUID
import psycopg
from .session_tokens import token_digest


def owned_ticket_state(conn: psycopg.Connection, token: str, ticket: UUID) -> dict | None:
    if not isinstance(token, str) or len(token) != 43:
        return None
    row = conn.execute(
        """SELECT t.id, t.state, t.version
           FROM user_session s
           JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
           JOIN account_customer_link l ON l.user_id=u.id AND l.revoked_at IS NULL
           JOIN service_ticket t ON t.customer_id=l.customer_id AND t.id=%s
           WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
           LIMIT 1""",
        (ticket,token_digest(token)),
    ).fetchone()
    return {"id":str(row[0]),"state":row[1],"version":row[2]} if row else None
