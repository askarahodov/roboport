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

