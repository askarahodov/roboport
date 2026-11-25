"""Scoped customer and work-order summary for synthetic auto intake (#166).

Never treat VIN as ownership proof. Only the exact verified, unrevoked
vehicle/customer link bound to the requested tenant location is returned.
"""
from uuid import UUID
import psycopg
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_stock_service import AutoStockDenied
from .auto_org_authority import organization_role_allows


def get_order_customer_summary(conn: psycopg.Connection, token: str,
                               organization_id: UUID, location_id: UUID,
                               order_id: UUID) -> dict:
    if type(token) is not str or not token.strip():
        raise AutoStockDenied("FORBIDDEN")
    if any(type(x) is not UUID for x in (organization_id, location_id, order_id)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        try:
            require_document_access(conn, token, organization_id, location_id,
                                    ("INTAKE", "MANAGER"), "INTAKE_ACT", "READ",
                                    order=order_id)
            # Базовое право INTAKE/MANAGER остаётся обязательным, но при
            # совмещении ролей нельзя обходить READ DENY или cutoff MECHANIC
            # через альтернативную проекцию того же акта.
            require_document_access(conn, token, organization_id, location_id,
                                    ("INTAKE", "MANAGER", "MECHANIC"),
                                    "INTAKE_ACT", "READ", order=order_id)
        except DocumentPolicyDenied:
            raise AutoStockDenied("FORBIDDEN") from None
        row = _scoped_order(conn, organization_id, location_id, order_id, identity=True)
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        order, appointment, vehicle, customer, link = row
        return {"orderId":str(order), "appointmentId":str(appointment),
                "vehicleId":str(vehicle), "customerId":str(customer),
                "customerLinkId":str(link)}


def _scoped_order(conn, organization_id, location_id, order_id, *, identity=False):
    # Only fixed internal projections. Timeline checks existence, not identity.
    projection = "w.id,a.id,a.vehicle_id,link.customer_id,link.id" if identity else "1"
    return conn.execute(
        f"""SELECT {projection}
           FROM auto_work_order w
           JOIN auto_appointment a ON a.id=w.appointment_id
             AND a.organization_id=w.organization_id
             AND a.location_id=w.location_id
           JOIN auto_vehicle_customer_link link ON link.id=a.customer_link_id
             AND link.vehicle_id=a.vehicle_id
             AND link.verified_at IS NOT NULL AND link.revoked_at IS NULL
           JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
             AND binding.location_id=a.location_id
           JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
           JOIN auto_service_location l ON l.id=w.location_id
             AND l.organization_id=o.id AND l.disabled_at IS NULL
           WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
           FOR SHARE OF w,a,link,binding,o,l""",
        (order_id,organization_id,location_id),
        ).fetchone()



def require_order_staff_scope(conn, token, organization_id, location_id, order_id):
    """Validate tenant, live staff and vehicle proof without returning identity.
    
    Caller must hold a transaction for the duration of its protected read.
    Document-specific READ must be enforced separately before reading events.
    """
    if (type(token) is not str or not token.strip() or
            any(type(x) is not UUID for x in (organization_id, location_id, order_id))):
        raise AutoStockDenied("FORBIDDEN")
    if not any(organization_role_allows(
            conn, token, organization_id, role, location_id, for_mutation=True)
            for role in ("INTAKE", "MANAGER")):
        raise AutoStockDenied("FORBIDDEN")
    if _scoped_order(conn, organization_id, location_id, order_id) is None:
        raise AutoStockDenied("FORBIDDEN")
