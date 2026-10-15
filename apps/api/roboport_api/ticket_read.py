"""Read minimal ticket state with authorization in the same SQL statement."""
from uuid import UUID
import psycopg
from .session_tokens import token_digest

# Must mirror 0001_core.STATES / ck_ticket_state.
TICKET_STATES = frozenset(['DRAFT','SUBMITTED','TRIAGE','AWAITING_DELIVERY','RECEIVED','DIAGNOSING','SAFETY_HOLD','ESTIMATE_DRAFT','AWAITING_APPROVAL','APPROVED','DECLINED','AWAITING_PARTS','REPAIRING','QC_PENDING','QC_FAILED','READY_FOR_HANDOVER','COMPLETED','CANCELLED','UNREPAIRABLE','WARRANTY_ROUTE'])


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


def owned_ticket_list(
    conn: psycopg.Connection, token: str, *, limit: int = 20,
    offset: int = 0, state: str | None = None,
) -> list[dict] | None:
    """None means unauthenticated; [] means authenticated with no visible tickets.

    Authorization and ticket selection share a SQL statement. Stable ordering avoids
    duplicates between pages for an unchanged data set.
    """
    if not isinstance(token, str) or len(token) != 43:
        return None
    if not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be 1..50")
    if not isinstance(offset, int) or not 0 <= offset <= 10000:
        raise ValueError("offset must be 0..10000")
    if state is not None and state not in TICKET_STATES:
        raise ValueError("unsupported ticket state")
    # Query session first; no access is granted by request headers or caller-supplied user IDs.
    from .session_lookup import session_subject
    subject = session_subject(conn, token)
    if subject is None:
        return None
    rows = conn.execute(
        """SELECT DISTINCT t.id, t.state, t.version, t.created_at
           FROM service_ticket t
           JOIN account_customer_link l ON l.customer_id=t.customer_id
             AND l.user_id=%s AND l.revoked_at IS NULL
           JOIN app_user u ON u.id=l.user_id AND u.status='ACTIVE'
           JOIN user_session s ON s.user_id=u.id AND s.token_hash=%s
             AND s.revoked_at IS NULL AND s.idle_expires_at>now()
             AND s.absolute_expires_at>now()
           WHERE (%s::text IS NULL OR t.state=%s)
           ORDER BY t.created_at DESC, t.id DESC
           LIMIT %s OFFSET %s""",
        (subject, token_digest(token), state, state, limit, offset),
    ).fetchall()
    return [{"id":str(row[0]), "state":row[1], "version":row[2]} for row in rows]


def owned_ticket_keyset_page(
    conn: psycopg.Connection, token: str, *, limit: int = 20,
    before_created_at=None, before_id: UUID | None = None,
    state: str | None = None,
) -> tuple[list[dict], tuple | None] | None:
    """Keyset pagination uses (created_at, id) rather than mutable offsets.

    A live session, active user and active ownership link are joined in the
    same statement as the ticket rows. Cursor input is never used as identity.
    """
    from datetime import datetime
    if not isinstance(token, str) or len(token) != 43:
        return None
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("invalid limit")
    if state is not None and state not in TICKET_STATES:
        raise ValueError("invalid state")
    if (before_created_at is None) != (before_id is None):
        raise ValueError("cursor must have both fields")
    if before_created_at is not None and (
        not isinstance(before_created_at, datetime)
        or before_created_at.tzinfo is None
        or not isinstance(before_id, UUID)
    ):
        raise ValueError("invalid cursor")
    # Start from a valid session and LEFT JOIN visible tickets. This preserves
    # the difference between a revoked session (zero rows) and an active
    # session with no visible tickets (one row with NULL ticket fields).
    # All authorization checks and ticket reads happen in ONE statement.
    rows = conn.execute(
        """SELECT DISTINCT t.id,t.state,t.version,t.created_at
           FROM user_session s
           JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
           LEFT JOIN account_customer_link l
             ON l.user_id=u.id AND l.revoked_at IS NULL
           LEFT JOIN service_ticket t
             ON t.customer_id=l.customer_id
             AND (%s::text IS NULL OR t.state=%s)
             AND (%s::timestamptz IS NULL OR
                  (t.created_at,t.id)<(%s::timestamptz,%s::uuid))
           WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
           ORDER BY t.created_at DESC NULLS LAST,t.id DESC NULLS LAST LIMIT %s""",
        (state,state,before_created_at,before_created_at,before_id,
         token_digest(token),limit+1),
    ).fetchall()
    if not rows:
        return None
    visible=[row for row in rows if row[0] is not None][:limit]
    next_cursor=(visible[-1][3],visible[-1][0]) if len(rows)>limit else None
    return ([{"id":str(v[0]),"state":v[1],"version":v[2]} for v in visible],next_cursor)
