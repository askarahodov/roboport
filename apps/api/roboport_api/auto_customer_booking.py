"""Только synthetic: self-service выбор поста и собственные записи клиента.

Никакой доступ по VIN; собственность проверяется по оригинальной verified-связи.
"""
from uuid import UUID
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


def my_appointments(conn: psycopg.Connection, token: str) -> list[dict]:
    with conn.transaction():
        actor = _actor(conn, token)
        rows = conn.execute(
            """SELECT a.id,a.organization_id,a.location_id,a.customer_link_id,
                      b.id,b.bay_id,b.starts_at,b.ends_at,b.canceled_at,
                      w.id,loc.timezone_name,
                      EXISTS (SELECT 1 FROM auto_work_order_service s
                              WHERE s.work_order_id=w.id AND s.origin='STAFF')
                      OR EXISTS (SELECT 1 FROM auto_work_order_inspection i
                                 WHERE i.work_order_id=w.id)
                      OR EXISTS (SELECT 1 FROM auto_work_order_estimate_revision r
                                 WHERE r.work_order_id=w.id)
                      OR EXISTS (SELECT 1 FROM auto_work_order_stock_event e
                                 WHERE e.work_order_id=w.id) AS started,
                      COALESCE((SELECT jsonb_agg(jsonb_build_object(
                        'serviceId',s.id,'title',s.title,'durationMinutes',s.duration_minutes
                      ) ORDER BY s.code,s.id)
                        FROM auto_work_order_service chosen
                        JOIN auto_service_catalog s ON s.id=chosen.service_id
                        WHERE chosen.work_order_id=w.id),'[]'::jsonb) AS services,
                      COALESCE((SELECT jsonb_agg(jsonb_build_object(
                        'skuId',sku.id,'article',sku.article,'kind',m.kind,
                        'quantity',m.quantity
                      ) ORDER BY sku.article,sku.id,m.kind)
                        FROM auto_work_order_material_request m
                        JOIN auto_stock_sku sku ON sku.id=m.sku_id
                        WHERE m.work_order_id=w.id),'[]'::jsonb) AS materials
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
               ORDER BY b.starts_at DESC,a.id DESC LIMIT 50""",
            (actor,),
        ).fetchall()
        return [{"appointmentId":str(a),"organizationId":str(o),"locationId":str(l),
                 "customerLinkId":str(link),"bookingId":str(booking),"bayId":str(bay),
                 "startsAt":start.isoformat(),"endsAt":end.isoformat(),
                 "status":"CANCELED" if canceled is not None else "SCHEDULED",
                 "workOrderId":str(w),"timeZone":zone,
                 "canManage":canceled is None and not started,
                 "services":services,"materials":materials}
                for a,o,l,link,booking,bay,start,end,canceled,w,zone,started,services,materials in rows]
