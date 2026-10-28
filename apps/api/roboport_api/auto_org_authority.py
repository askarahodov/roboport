"""Fail-closed organization authorization for future automotive endpoints.

Not connected to public routes: no bootstrap or user-management API yet.
"""
from uuid import UUID
import psycopg
from .session_lookup import session_subject

ROLE_CODES = frozenset({"MANAGER", "INTAKE", "MECHANIC", "WAREHOUSE"})


def organization_role_allows(conn: psycopg.Connection, token: str,
                             organization_id: UUID, role: str,
                             location_id: UUID | None = None,
                             *, for_mutation: bool = False) -> bool:
    """A grant is not authority without a live session, tenant and branch.

    The caller must hold a transaction for for_mutation=True. The session,
    organization, location and grant are locked to serialize with revocations.
    This deliberately does not consult the legacy user_role table.
    """
    if (not isinstance(organization_id, UUID)
            or (location_id is not None and not isinstance(location_id, UUID))
            or role not in ROLE_CODES or not isinstance(token, str)):
        return False
    actor = session_subject(conn, token, for_mutation=for_mutation)
    if actor is None:
        return False
    lock = " FOR SHARE OF o, g, l" if for_mutation and location_id else (
        " FOR SHARE OF o, g" if for_mutation else ""
    )
    row = conn.execute(
        """SELECT g.id FROM auto_organization_role_grant g
           JOIN auto_organization o ON o.id=g.organization_id
           LEFT JOIN auto_service_location l ON l.id=g.location_id
           WHERE g.organization_id=%s AND g.user_id=%s AND g.role_code=%s
             AND g.revoked_at IS NULL AND o.disabled_at IS NULL
             AND ((%s::uuid IS NULL AND g.location_id IS NULL)
                  OR (%s::uuid IS NOT NULL AND
                      (g.location_id=%s OR g.location_id IS NULL)))
             AND (%s::uuid IS NULL OR EXISTS (
                SELECT 1 FROM auto_service_location scope
                WHERE scope.id=%s AND scope.organization_id=o.id
                  AND scope.disabled_at IS NULL))
             AND (g.location_id IS NULL OR
                  (l.organization_id=o.id AND l.disabled_at IS NULL))
           LIMIT 1""" + lock,
        (organization_id, actor, role, location_id, location_id, location_id,
         location_id, location_id),
    ).fetchone()
    return row is not None
