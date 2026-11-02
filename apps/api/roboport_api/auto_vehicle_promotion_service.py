"""Explicit synthetic-only, authorized vehicle promotion; never transfers ownership/history."""
from uuid import UUID, uuid4
import psycopg

from roboport_api.auto_org_authority import organization_role_allows


class VehiclePromotionDenied(ValueError):
    """Generic error: never return VIN, source IDs or customer history."""


def promote_synthetic_vehicles(conn: psycopg.Connection, token: str, *,
                               organization_id: UUID, location_id: UUID,
                               batch_id: UUID, synthetic_only: bool = False) -> dict:
    if (synthetic_only is not True or type(token) is not str or not token
            or any(type(x) is not UUID for x in (organization_id, location_id, batch_id))):
        raise VehiclePromotionDenied("FORBIDDEN")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, "MANAGER",
                                        location_id, for_mutation=True):
            raise VehiclePromotionDenied("FORBIDDEN")
        batch = conn.execute("""
            SELECT row_count FROM auto_vehicle_migration_batch
            WHERE id=%s AND organization_id=%s FOR UPDATE
        """, (batch_id, organization_id)).fetchone()
        if batch is None:
            raise VehiclePromotionDenied("BATCH_UNAVAILABLE")
        lines = conn.execute("""
            SELECT row_number,vin,make,model FROM auto_vehicle_migration_line
            WHERE batch_id=%s AND organization_id=%s AND location_id=%s
            ORDER BY row_number
        """, (batch_id, organization_id, location_id)).fetchall()
        if len(lines) != batch[0] or not lines:
            raise VehiclePromotionDenied("BATCH_SCOPE_MISMATCH")
        existing = conn.execute("""
            SELECT p.row_number, p.vehicle_id, v.vin, v.make, v.model
            FROM auto_vehicle_migration_promotion p
            JOIN auto_vehicle v ON v.id=p.vehicle_id
            WHERE p.batch_id=%s ORDER BY p.row_number
        """, (batch_id,)).fetchall()
        if existing:
            if len(existing) != len(lines) or any(
                (old[0], old[2], old[3], old[4]) != current
                for old, current in zip(existing, lines)
            ):
                raise VehiclePromotionDenied("PROMOTION_MISMATCH")
            return {"ok": True, "replayed": True, "count": len(lines), "writes": 0,
                    "mode": "SYNTHETIC_VEHICLE_PROMOTION"}
        if conn.execute("""
            SELECT 1 FROM auto_vehicle WHERE vin=ANY(%s::text[]) LIMIT 1
        """, ([line[1] for line in lines],)).fetchone():
            raise VehiclePromotionDenied("VIN_CONFLICT")
        try:
            with conn.transaction():
                for number, vin, make, model in lines:
                    vehicle_id = uuid4()
                    conn.execute("""
                        INSERT INTO auto_vehicle(id,vin,make,model)
                        VALUES (%s,%s,%s,%s)
                    """, (vehicle_id, vin, make, model))
                    conn.execute("""
                        INSERT INTO auto_vehicle_migration_promotion
                          (batch_id,row_number,vehicle_id)
                        VALUES (%s,%s,%s)
                    """, (batch_id, number, vehicle_id))
        except (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation):
            raise VehiclePromotionDenied("VIN_CONFLICT") from None
    return {"ok": True, "replayed": False, "count": len(lines),
            "writes": 2 * len(lines), "mode": "SYNTHETIC_VEHICLE_PROMOTION"}
