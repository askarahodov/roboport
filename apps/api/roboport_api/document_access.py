"""Private document-read gate: current session, ticket ownership and file state."""
from uuid import UUID, uuid4
import psycopg
from .session_tokens import token_digest


def authorize_attachment_read(conn: psycopg.Connection, token: str, attachment_id: UUID) -> tuple[str,str,str,int] | None:
    """Never authorize from object_key alone; quarantine/role revoke applies immediately."""
    if not isinstance(token,str) or len(token)!=43 or not isinstance(attachment_id,UUID):
        return None
    row=conn.execute(
        """SELECT a.object_key,a.mime,a.sha256,a.byte_size
           FROM attachment a
           JOIN service_ticket t ON t.id=a.ticket_id
           JOIN user_session s ON s.token_hash=%s
             AND s.revoked_at IS NULL AND s.idle_expires_at>now()
             AND s.absolute_expires_at>now()
           JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
           WHERE a.id=%s AND a.upload_state='READY'
             AND a.quarantine_state='CLEAR'
             AND (
                EXISTS(SELECT 1 FROM user_role ur
                  JOIN account_customer_link l ON l.user_id=ur.user_id
                    AND l.customer_id=t.customer_id AND l.revoked_at IS NULL
                  WHERE ur.user_id=u.id AND ur.role_code='CLIENT'
                    AND ur.revoked_at IS NULL
                    AND (ur.expires_at IS NULL OR ur.expires_at>now()))
                OR EXISTS(SELECT 1 FROM user_role ur
                  JOIN acl_entry e ON e.user_id=ur.user_id
                    AND e.ticket_id=t.id AND e.action='ticket.read'
                    AND e.revoked_at IS NULL
                    AND (e.expires_at IS NULL OR e.expires_at>now())
                  WHERE ur.user_id=u.id AND ur.role_code IN ('INTAKE','ENGINEER','MANAGER')
                    AND ur.revoked_at IS NULL
                    AND (ur.expires_at IS NULL OR ur.expires_at>now()))
             )""",
        (token_digest(token),attachment_id),
    ).fetchone()
    return row if row else None


def audited_attachment_gate(conn: psycopg.Connection, token: str, attachment_id: UUID):
    """Audit a decision with no object key, session, or PII in the event."""
    from .session_lookup import session_subject
    actor=session_subject(conn,token)
    row=authorize_attachment_read(conn,token,attachment_id)
    conn.execute(
        """INSERT INTO authorization_audit(id,actor_id,action,outcome)
           VALUES (%s,%s,'attachment.read',%s)""",
        (uuid4(),actor,'ALLOW' if row else 'DENY'),
    )
    return row


def authorize_attachment_upload(conn: psycopg.Connection, token: str, ticket_id: UUID) -> UUID | None:
    """Return current actor ONLY for a live session with ticket-specific write permission."""
    if not isinstance(token,str) or len(token)!=43 or not isinstance(ticket_id,UUID):
        return None
    row=conn.execute(
        """SELECT u.id FROM user_session s
           JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
           JOIN service_ticket t ON t.id=%s
           WHERE s.token_hash=%s AND s.revoked_at IS NULL
             AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
             AND t.state NOT IN ('SAFETY_HOLD','QC_FAILED')
             AND (
               EXISTS (SELECT 1 FROM user_role r
                 JOIN account_customer_link l ON l.user_id=r.user_id
                   AND l.customer_id=t.customer_id AND l.revoked_at IS NULL
                 WHERE r.user_id=u.id AND r.role_code='CLIENT'
                   AND r.revoked_at IS NULL
                   AND (r.expires_at IS NULL OR r.expires_at>now()))
               OR EXISTS (SELECT 1 FROM user_role r
                 JOIN acl_entry a ON a.user_id=r.user_id
                   AND a.ticket_id=t.id AND a.action IN ('ticket.intake','ticket.manage')
                   AND a.revoked_at IS NULL
                   AND (a.expires_at IS NULL OR a.expires_at>now())
                 WHERE r.user_id=u.id AND r.role_code IN ('INTAKE','MANAGER')
                   AND r.revoked_at IS NULL
                   AND (r.expires_at IS NULL OR r.expires_at>now()))
             )""",
        (ticket_id,token_digest(token)),
    ).fetchone()
    return row[0] if row else None
