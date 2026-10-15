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
