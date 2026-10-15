"""Серверные RBAC+ACL правила; deny by default, без доверия к HTTP-заголовкам.

Входом служит только серверный токен сессии; эта функция не публикует HTTP.
"""
from uuid import UUID
import psycopg
from .session_tokens import token_digest

PERMISSIONS = {
    "PUBLIC": frozenset(),
    "CLIENT": frozenset(("ticket.read",)),
    "INTAKE": frozenset(("ticket.read", "ticket.intake")),
    "ENGINEER": frozenset(("ticket.read", "ticket.diagnose")),
    "STOCK": frozenset(),
    "MANAGER": frozenset(("ticket.read", "ticket.manage")),
    "FINANCE": frozenset(),
    "ADMIN": frozenset(),  # роль администратора не даёт доступ к чужим PII
    "AUDITOR": frozenset(),
}

def authorize_ticket(conn: psycopg.Connection, token: str, action: str, ticket_id: UUID) -> bool:
    """Проверка сессии, роли, связи/назначения и состояния в одном SQL.

    Права клиента относятся к заявке, а не к текущему владельцу устройства:
    история прежнего владельца не наследуется при продаже устройства.
    """
    if (not isinstance(token, str) or len(token) != 43
            or action not in frozenset().union(*PERMISSIONS.values())
            or not isinstance(ticket_id, UUID)):
        return False
    # Roles and grants are resolved live on every call; no stale cached role.
    row = conn.execute(
        """SELECT t.state,
            EXISTS(SELECT 1 FROM user_role ur
              WHERE ur.user_id=u.id AND ur.role_code='CLIENT'
                AND ur.revoked_at IS NULL
                AND (ur.expires_at IS NULL OR ur.expires_at>now())
                AND EXISTS(SELECT 1 FROM account_customer_link l
                  WHERE l.user_id=u.id AND l.customer_id=t.customer_id
                    AND l.revoked_at IS NULL)) AS client_access,
            EXISTS(SELECT 1 FROM user_role ur
              JOIN acl_entry a ON a.user_id=ur.user_id AND a.ticket_id=t.id
                AND a.action=%s AND a.revoked_at IS NULL
                AND (a.expires_at IS NULL OR a.expires_at>now())
              WHERE ur.user_id=u.id AND ur.revoked_at IS NULL
                AND (ur.expires_at IS NULL OR ur.expires_at>now())
                AND ((ur.role_code='INTAKE' AND %s IN ('ticket.read','ticket.intake'))
                  OR (ur.role_code='ENGINEER' AND %s IN ('ticket.read','ticket.diagnose'))
                  OR (ur.role_code='MANAGER' AND %s IN ('ticket.read','ticket.manage')))
            ) AS assigned_access
          FROM user_session s
          JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
          JOIN service_ticket t ON t.id=%s
          WHERE s.token_hash=%s AND s.revoked_at IS NULL
            AND s.idle_expires_at>now() AND s.absolute_expires_at>now()""",
        (action,action,action,action,ticket_id,token_digest(token)),
    ).fetchone()
    if row is None:
        return False
    state, client_allowed, delegated_allowed = row
    if action == "ticket.read":
        return bool(client_allowed or delegated_allowed)
    if action == "ticket.diagnose" and state == "SAFETY_HOLD":
        return False
    if action == "ticket.manage" and state in ("SAFETY_HOLD", "QC_FAILED"):
        return False
    return bool(delegated_allowed)


def authorized_ticket_read_audited(
    conn: psycopg.Connection, token: str, ticket_id: UUID,
    *, action: str = "ticket.read",
) -> bool:
    """Audit an authorization decision without storing tokens or PII.

    Caller must use a dedicated transaction and commit both decision and read
    as one unit; the audit record stores only actor UUID and ticket UUID.
    """
    from uuid import uuid4
    from .session_lookup import session_subject
    actor = session_subject(conn, token)
    allowed = authorize_ticket(conn, token, action, ticket_id)
    conn.execute(
        """INSERT INTO authorization_audit(id,actor_id,ticket_id,action,outcome)
           VALUES (%s,%s,%s,%s,%s)""",
        (uuid4(), actor, ticket_id if allowed else None, action,
         "ALLOW" if allowed else "DENY"),
    )
    return allowed


def authorize_estimate(
    conn: psycopg.Connection, token: str, action: str, estimate_id: UUID,
) -> bool:
    """One-statement estimate ACL; client can only act on latest SENT estimate."""
    if (not isinstance(token,str) or len(token)!=43 or not isinstance(estimate_id,UUID)
            or action not in ("estimate.read","estimate.approve")):
        return False
    row=conn.execute(
        """SELECT e.state,
          e.version=(SELECT MAX(version) FROM estimate WHERE ticket_id=e.ticket_id) AS latest,
          EXISTS(SELECT 1 FROM user_role r
            JOIN account_customer_link l ON l.user_id=r.user_id
              AND l.customer_id=t.customer_id AND l.revoked_at IS NULL
            WHERE r.user_id=u.id AND r.role_code='CLIENT'
              AND r.revoked_at IS NULL
              AND (r.expires_at IS NULL OR r.expires_at>now())) AS client_permission,
          EXISTS(SELECT 1 FROM user_role r
            JOIN acl_entry a ON a.user_id=r.user_id
              AND a.ticket_id=t.id AND a.action=%s
              AND a.revoked_at IS NULL
              AND (a.expires_at IS NULL OR a.expires_at>now())
            WHERE r.user_id=u.id AND r.revoked_at IS NULL
              AND (r.expires_at IS NULL OR r.expires_at>now())
              AND ((r.role_code='FINANCE' AND %s='estimate.read')
                OR (r.role_code='MANAGER' AND %s IN ('estimate.read','estimate.approve'))))
            AS delegated_permission
         FROM user_session s
         JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
         JOIN estimate e ON e.id=%s
         JOIN service_ticket t ON t.id=e.ticket_id
         WHERE s.token_hash=%s AND s.revoked_at IS NULL
           AND s.idle_expires_at>now() AND s.absolute_expires_at>now()""",
        (action,action,action,estimate_id,token_digest(token)),
    ).fetchone()
    if row is None:
        return False
    state,latest,client,delegated=row
    if action=="estimate.read":
        return bool(delegated or (client and state in ("SENT","APPROVED","DECLINED")))
    return bool(latest and state=="SENT" and (client or delegated))
