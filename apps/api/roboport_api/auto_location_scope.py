"""Optional fail-closed synthetic location check for auto staff operations.

Not a production manager bootstrap: a global user_role and a per-vehicle
auto_vehicle_staff_assignment are always required separately by callers.
"""
import os
from uuid import UUID

import psycopg


def location_scope_allows(
    conn: psycopg.Connection, actor: UUID, role: str, vehicle: UUID,
) -> bool:
    """Check additional branch entitlement and lock it through transaction end.

    Gate off (default) preserves current synthetic fixtures. Any invalid gate
    value denies, rather than silently disabling the additional restriction.
    Gate on: vehicle must have immutable location binding, location active,
    and an active location-scoped staff grant for precisely actor+role.
    """
    flag = os.getenv("ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE", "0")
    if flag == "0":
        return True
    if flag != "1" or role not in ("INTAKE", "MANAGER"):
        return False
    row = conn.execute(
        """SELECT g.id
           FROM auto_vehicle_location_binding b
           JOIN auto_service_location l ON l.id=b.location_id
             AND l.disabled_at IS NULL
           JOIN auto_location_role_grant g ON g.location_id=l.id
             AND g.user_id=%s AND g.role_code=%s AND g.revoked_at IS NULL
           WHERE b.vehicle_id=%s
           ORDER BY g.created_at DESC LIMIT 1
           FOR SHARE OF b,l,g""",
        (actor, role, vehicle),
    ).fetchone()
    return row is not None
