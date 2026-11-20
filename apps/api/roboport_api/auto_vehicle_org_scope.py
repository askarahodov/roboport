"""Проверка филиального полномочия для автомобиля, закреплённого за организацией."""

from uuid import UUID

import psycopg

from .auto_org_authority import organization_role_allows


def vehicle_organization_scope(
    conn: psycopg.Connection, vehicle_id: UUID,
) -> tuple[UUID, UUID] | None:
    """Вернуть неизменяемые организацию и филиал автомобиля, если филиал назначен."""
    if type(vehicle_id) is not UUID:
        return None
    row = conn.execute(
        """SELECT l.organization_id,b.location_id
           FROM auto_vehicle_location_binding b
           JOIN auto_service_location l ON l.id=b.location_id
           WHERE b.vehicle_id=%s
           FOR SHARE OF b,l""",
        (vehicle_id,),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


def organization_vehicle_role_allows(
    conn: psycopg.Connection, token: str, role: str, vehicle_id: UUID,
) -> bool | None:
    """Проверить роль в организации филиала; None означает только legacy fixture."""
    scope = vehicle_organization_scope(conn, vehicle_id)
    if scope is None:
        return None
    organization_id, location_id = scope
    return organization_role_allows(
        conn, token, organization_id, role, location_id, for_mutation=True,
    )


def organization_vehicle_target_has_role(
    conn: psycopg.Connection, target_id: UUID, role: str, vehicle_id: UUID,
) -> bool | None:
    """Проверить активный филиальный grant адресата без наследования user_role."""
    scope = vehicle_organization_scope(conn, vehicle_id)
    if scope is None:
        return None
    organization_id, location_id = scope
    row = conn.execute(
        """SELECT g.id
           FROM auto_organization_role_grant g
           JOIN auto_organization o ON o.id=g.organization_id
           JOIN auto_service_location l
             ON l.id=%s AND l.organization_id=o.id
           JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
           WHERE g.organization_id=%s AND g.user_id=%s AND g.role_code=%s
             AND g.revoked_at IS NULL AND o.disabled_at IS NULL
             AND l.disabled_at IS NULL
             AND (g.location_id IS NULL OR g.location_id=l.id)
           LIMIT 1 FOR SHARE OF g,o,l,u""",
        (location_id, organization_id, target_id, role),
    ).fetchone()
    return row is not None
