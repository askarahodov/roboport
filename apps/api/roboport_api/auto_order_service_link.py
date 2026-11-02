"""Authorize synthetic catalog service selection for existing work orders."""
from uuid import UUID, uuid4
import psycopg
from roboport_api.auto_org_authority import organization_role_allows

class OrderServiceDenied(ValueError):
    pass

def add_synthetic_order_service(conn, token: str, *,
                                organization_id: UUID, location_id: UUID,
                                work_order_id: UUID, service_id: UUID,
                                synthetic_only: bool = False) -> dict:
    if (synthetic_only is not True or type(token) is not str or not token
        or any(type(v) is not UUID for v in
               (organization_id,location_id,work_order_id,service_id))):
        raise OrderServiceDenied("FORBIDDEN")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id,
                                        "MANAGER", location_id, for_mutation=True):
            raise OrderServiceDenied("FORBIDDEN")
        # Recheck proof and booking at every mutation, including replay, not just
        # at appointment creation. The SQL trigger independently enforces this.
        work_order = conn.execute("""
            SELECT w.id FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN auto_bay_booking b ON b.id=a.booking_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
              AND b.organization_id=w.organization_id
              AND b.location_id=w.location_id AND b.canceled_at IS NULL
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            FOR UPDATE OF w
        """, (work_order_id,organization_id,location_id)).fetchone()
        catalog = conn.execute("""
            SELECT id FROM auto_service_catalog WHERE id=%s
            AND organization_id=%s AND location_id=%s FOR SHARE
        """, (service_id,organization_id,location_id)).fetchone()
        if work_order is None or catalog is None:
            raise OrderServiceDenied("SCOPE_UNAVAILABLE")
        existing = conn.execute("""
            SELECT id FROM auto_work_order_service
            WHERE work_order_id=%s AND service_id=%s
        """, (work_order_id, service_id)).fetchone()
        if existing:
            return {"ok": True, "replayed": True, "writes": 0,
                    "mode": "SYNTHETIC_ORDER_SERVICE"}
        try:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO auto_work_order_service
                    (id,work_order_id,service_id,organization_id,location_id)
                    VALUES (%s,%s,%s,%s,%s)
                """, (uuid4(),work_order_id,service_id,organization_id,location_id))
        except (psycopg.errors.UniqueViolation, psycopg.errors.ForeignKeyViolation):
            raise OrderServiceDenied("SCOPE_UNAVAILABLE") from None
    return {"ok": True, "replayed": False, "writes": 1,
            "mode": "SYNTHETIC_ORDER_SERVICE"}
