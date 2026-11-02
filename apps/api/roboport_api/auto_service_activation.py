"""Activate verified synthetic SERVICE staging into a branch-scoped catalog.

No pricing, customer identity, work orders or repair history is ever imported.
"""
from uuid import UUID, uuid4

import psycopg

from roboport_api.auto_org_authority import organization_role_allows
from roboport_api.auto_service_import import ServiceImportDenied


def activate_synthetic_services(conn: psycopg.Connection, token: str, *,
                                organization_id: UUID, location_id: UUID,
                                batch_id: UUID, synthetic_only: bool = False) -> dict:
    if (synthetic_only is not True or type(token) is not str or not token
            or any(type(x) is not UUID for x in
                   (organization_id, location_id, batch_id))):
        raise ServiceImportDenied("FORBIDDEN")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, "MANAGER",
                                        location_id, for_mutation=True):
            raise ServiceImportDenied("FORBIDDEN")
        batch = conn.execute("""
            SELECT row_count FROM auto_service_import_batch
            WHERE id=%s AND organization_id=%s AND location_id=%s
            FOR UPDATE
        """, (batch_id, organization_id, location_id)).fetchone()
        if batch is None:
            raise ServiceImportDenied("BATCH_UNAVAILABLE")
        lines = conn.execute("""
            SELECT row_number,code,title,duration_minutes
            FROM auto_service_import_line WHERE batch_id=%s ORDER BY row_number
        """, (batch_id,)).fetchall()
        if len(lines) != batch[0] or not lines:
            raise ServiceImportDenied("BATCH_INCOMPLETE")
        active = conn.execute("""
            SELECT a.row_number,c.code,c.title,c.duration_minutes,
                   c.organization_id,c.location_id
            FROM auto_service_import_activation a
            JOIN auto_service_catalog c ON c.id=a.service_id
            WHERE a.batch_id=%s ORDER BY a.row_number
        """, (batch_id,)).fetchall()
        if active:
            if len(active) != len(lines) or any(
                entry != (*line, organization_id, location_id)
                for entry, line in zip(active, lines)
            ):
                raise ServiceImportDenied("ACTIVATION_MISMATCH")
            return {"ok": True, "replayed": True, "count": len(lines),
                    "writes": 0, "mode": "SYNTHETIC_SERVICE_ACTIVATION"}
        # A code already used at this branch must never silently become a new
        # service or inherit somebody else's catalog identity.
        if conn.execute("""
            SELECT 1 FROM auto_service_catalog
            WHERE location_id=%s AND code=ANY(%s::text[]) LIMIT 1
        """, (location_id, [row[1] for row in lines])).fetchone():
            raise ServiceImportDenied("SERVICE_CONFLICT")
        try:
            with conn.transaction():
                for number, code, title, minutes in lines:
                    service_id = uuid4()
                    conn.execute("""
                        INSERT INTO auto_service_catalog(
                            id,organization_id,location_id,code,title,duration_minutes)
                        VALUES(%s,%s,%s,%s,%s,%s)
                    """, (service_id, organization_id, location_id,
                          code, title, minutes))
                    conn.execute("""
                        INSERT INTO auto_service_import_activation(
                            batch_id,row_number,service_id)
                        VALUES(%s,%s,%s)
                    """, (batch_id, number, service_id))
        except (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation):
            raise ServiceImportDenied("SERVICE_CONFLICT") from None
    return {"ok": True, "replayed": False, "count": len(lines),
            "writes": len(lines) * 2, "mode": "SYNTHETIC_SERVICE_ACTIVATION"}
