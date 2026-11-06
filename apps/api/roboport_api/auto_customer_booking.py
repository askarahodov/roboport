"""Только synthetic: self-service выбор поста и собственные записи клиента.

Никакой доступ по VIN; собственность проверяется по оригинальной verified-связи.
"""
import hashlib
from uuid import UUID
from .keyset_cursor import encode_cursor, decode_cursor
import psycopg

from .auto_customer_orders import _actor, CustomerOrderDenied
from .auto_appointment_service import AppointmentDenied


def bookable_resources(conn: psycopg.Connection, token: str) -> list[dict]:
    with conn.transaction():
        actor = _actor(conn, token)
        rows = conn.execute(
            """SELECT org.id,loc.id,proof.id,bay.id,v.make,v.model,loc.timezone_name
               FROM account_customer_link acl
               JOIN auto_vehicle_customer_link proof ON proof.customer_id=acl.customer_id
                 AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
                 AND proof.revoked_at IS NULL
               JOIN auto_vehicle v ON v.id=proof.vehicle_id
               JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=v.id
               JOIN auto_service_location loc ON loc.id=binding.location_id
                 AND loc.disabled_at IS NULL
               JOIN auto_organization org ON org.id=loc.organization_id
                 AND org.disabled_at IS NULL
               JOIN auto_work_bay bay ON bay.location_id=loc.id
                 AND bay.organization_id=org.id AND bay.disabled_at IS NULL
               WHERE acl.user_id=%s AND acl.revoked_at IS NULL
               ORDER BY loc.id,proof.id,bay.id LIMIT 50""",
            (actor,),
        ).fetchall()
        return [{"organizationId":str(o),"locationId":str(l),
                 "customerLinkId":str(p),"bayId":str(b),
                 "vehicle":{"make":make,"model":model},"timeZone":tz}
                for o,l,p,b,make,model,tz in rows]


def my_appointments(conn: psycopg.Connection, token: str, *, cursor: str | None = None) -> dict:
    """Keyset page of at most 50 owned appointments; recheck live ACL per page."""
    if cursor is not None and (type(cursor) is not str or not cursor or len(cursor) > 256):
        raise ValueError("INVALID_CURSOR")
    with conn.transaction():
        actor = _actor(conn, token)
        scope = hashlib.sha256(("customer-bookings:v1:" + str(actor)).encode()).hexdigest()[:32]
        try:
            position = decode_cursor(cursor, scope, token) if cursor is not None else None
        except ValueError:
            raise ValueError("INVALID_CURSOR") from None
        rows = conn.execute(
            """SELECT a.id,a.organization_id,a.location_id,a.customer_link_id,
                      b.id,b.bay_id,b.starts_at,b.ends_at,b.canceled_at,
                      w.id,loc.timezone_name,
                      EXISTS (SELECT 1 FROM auto_work_order_service s
                              WHERE s.work_order_id=w.id AND s.selection_origin<>'BOOKING')
                      OR EXISTS (SELECT 1 FROM auto_work_order_inspection i
                                 WHERE i.work_order_id=w.id)
                      OR EXISTS (SELECT 1 FROM auto_work_order_estimate_revision r
                                 WHERE r.work_order_id=w.id)
                      OR EXISTS (
                        SELECT 1 FROM auto_work_order_stock_event e
                        WHERE e.work_order_id=w.id
                        GROUP BY e.work_order_id
                        HAVING SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
                                        WHEN e.action IN ('ISSUE','RELEASE') THEN -e.quantity
                                        ELSE 0 END)>0
                            OR SUM(CASE WHEN e.action='ISSUE' THEN e.quantity
                          WHEN e.action='RETURN' THEN -e.quantity
                          ELSE 0 END) > 0
                      ) AS started,
                      (SELECT COALESCE(array_agg(s.service_id ORDER BY s.service_id),
                                       ARRAY[]::uuid[])
                       FROM auto_work_order_service s
                       WHERE s.work_order_id=w.id) AS service_ids
               FROM auto_appointment a
               JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
                 AND proof.vehicle_id=a.vehicle_id
                 AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
                 AND proof.revoked_at IS NULL
               JOIN account_customer_link acl ON acl.customer_id=proof.customer_id
                 AND acl.user_id=%s AND acl.revoked_at IS NULL
               JOIN auto_vehicle_location_binding binding
                 ON binding.vehicle_id=a.vehicle_id AND binding.location_id=a.location_id
               JOIN auto_organization org ON org.id=a.organization_id
                 AND org.disabled_at IS NULL
               JOIN auto_service_location loc ON loc.id=a.location_id
                 AND loc.organization_id=org.id AND loc.disabled_at IS NULL
               JOIN auto_work_order w ON w.appointment_id=a.id
                 AND w.organization_id=org.id AND w.location_id=loc.id
               JOIN LATERAL (
                 SELECT COALESCE((
                   SELECT r.new_booking_id FROM auto_appointment_reschedule r
                   WHERE r.appointment_id=a.id
                   ORDER BY r.created_at DESC,r.id DESC LIMIT 1
                 ),a.booking_id) AS current_id
               ) latest ON TRUE
               JOIN auto_bay_booking b ON b.id=latest.current_id
                 AND b.organization_id=org.id AND b.location_id=loc.id
               AND (%s::timestamptz IS NULL OR (b.starts_at,a.id)<(%s::timestamptz,%s::uuid))
               ORDER BY b.starts_at DESC,a.id DESC LIMIT 51""",
            (actor, position[0] if position else None,
             position[0] if position else None, position[1] if position else None),
        ).fetchall()
        page = rows[:50]
        next_cursor = (encode_cursor(page[-1][6], page[-1][0], scope, token)
                       if len(rows) > 50 else None)
        return {"items":[{"appointmentId":str(a),"organizationId":str(o),"locationId":str(l),
                 "customerLinkId":str(link),"bookingId":str(booking),"bayId":str(bay),
                 "startsAt":start.isoformat(),"endsAt":end.isoformat(),
                 "status":"CANCELED" if canceled is not None else "SCHEDULED",
                 "workOrderId":str(w),"timeZone":zone,
                 "canManage":canceled is None and not started,
                 "serviceIds":[str(service) for service in service_ids]}
                for a,o,l,link,booking,bay,start,end,canceled,w,zone,started,service_ids in page],
                "nextCursor": next_cursor, "syntheticOnly": True}
