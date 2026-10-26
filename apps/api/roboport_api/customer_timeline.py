"""Owner-bound event history, no staff actor or previous-owner information."""
from uuid import UUID
import psycopg
from .session_tokens import token_digest

def owned_ticket_timeline(conn:psycopg.Connection, token:str, ticket_id:UUID):
    if not isinstance(token,str) or len(token)!=43:
        return None
    # Validate the live session AND active ownership in the same SQL snapshot.
    rows=conn.execute("""
        SELECT t.id, e.from_state,e.to_state,e.expected_version,e.created_at
        FROM user_session s
        JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
        JOIN account_customer_link l ON l.user_id=u.id AND l.revoked_at IS NULL
        JOIN service_ticket t ON t.customer_id=l.customer_id AND t.id=%s
        LEFT JOIN LATERAL (
          SELECT from_state,to_state,expected_version,created_at
          FROM ticket_state_event
          WHERE ticket_id=t.id
          ORDER BY created_at ASC,id ASC LIMIT 50
        ) e ON true
        WHERE s.token_hash=%s AND s.revoked_at IS NULL
          AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
        ORDER BY e.created_at ASC NULLS LAST
    """,(ticket_id,token_digest(token))).fetchall()
    if not rows:
        return None
    return [{"fromState":r[1],"toState":r[2],"version":r[3]+1,
             "at":r[4].isoformat()} for r in rows if r[2] is not None]
