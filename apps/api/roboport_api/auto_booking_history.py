"""История записи с исходным WorkOrder и состоянием синтетической очереди."""
from hashlib import sha256

from .auto_org_authority import organization_role_allows
from .auto_appointment_service import _customer_scope, AppointmentDenied, current_appointment_booking
from .keyset_cursor import encode_cursor,decode_cursor


class BookingHistoryDenied(ValueError):
    pass


def booking_history(conn,token,org,loc,appointment,*,customer=False,cursor=None):
    with conn.transaction():
        if not customer and not organization_role_allows(
                conn,token,org,"INTAKE",loc,for_mutation=True):
            raise BookingHistoryDenied("FORBIDDEN")
        # Блокируем identity: перенос и отмена не меняют историю в середине чтения.
        row=conn.execute("""
            SELECT a.booking_id,a.customer_link_id,w.id,l.timezone_name,
                a.id
            FROM auto_appointment a JOIN auto_work_order w ON w.appointment_id=a.id
              AND w.organization_id=a.organization_id AND w.location_id=a.location_id
            JOIN auto_service_location l ON l.id=a.location_id
              AND l.organization_id=a.organization_id AND l.disabled_at IS NULL
            JOIN auto_organization o ON o.id=a.organization_id AND o.disabled_at IS NULL
            JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
              AND proof.vehicle_id=a.vehicle_id AND proof.verified_at IS NOT NULL
              AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
            JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
              AND binding.location_id=a.location_id
            WHERE a.id=%s AND a.organization_id=%s AND a.location_id=%s
            FOR SHARE OF a,proof,binding
        """,(appointment,org,loc)).fetchone()
        if row is None:
            raise BookingHistoryDenied("FORBIDDEN")
        initial,link,order,zone,_=row
        if customer:
            try:
                _customer_scope(conn,token,org,loc,link)
            except AppointmentDenied as exc:
                raise BookingHistoryDenied("FORBIDDEN") from exc
        # После захвата блокировки повторно разрешаем актуальную бронь.
        current=current_appointment_booking(conn,appointment)
        scope=sha256(f"booking-history:v1:{org}:{loc}:{appointment}:{customer}".encode()).hexdigest()[:32]
        position=decode_cursor(cursor,scope,token) if cursor is not None else (None,None)
        rows=conn.execute("""
            WITH receipts AS (
                SELECT %s::uuid AS booking_id, 'CREATED'::text AS action,
                    'auto-appointment:event:'||e.id::text AS event_key
                FROM auto_appointment_event e WHERE e.appointment_id=%s AND e.action='CREATED'
                UNION ALL
                SELECT r.new_booking_id,'RESCHEDULED','auto-appointment:move:'||r.id::text
                FROM auto_appointment_reschedule r WHERE r.appointment_id=%s
            )
            SELECT b.id,bay.code,b.starts_at,b.ends_at,b.created_at,b.canceled_at,
                receipts.action,n.status,n.attempts
            FROM receipts JOIN auto_bay_booking b ON b.id=receipts.booking_id
                AND b.organization_id=%s AND b.location_id=%s
            JOIN auto_work_bay bay ON bay.id=b.bay_id
                AND bay.organization_id=b.organization_id AND bay.location_id=b.location_id
            LEFT JOIN notification_outbox n ON n.event_key=receipts.event_key
                AND n.aggregate_type='auto_appointment' AND n.aggregate_id=%s
            WHERE (%s::timestamptz IS NULL OR (b.created_at,b.id)<(%s::timestamptz,%s::uuid))
            ORDER BY b.created_at DESC,b.id DESC LIMIT 51
        """,(initial,appointment,appointment,org,loc,appointment,
               position[0],position[0],position[1])).fetchall()
        cancellation=conn.execute("""
            SELECT e.recorded_at,n.status,n.attempts FROM auto_appointment_event e
            LEFT JOIN notification_outbox n ON n.event_key='auto-appointment:event:'||e.id::text
                AND n.aggregate_type='auto_appointment' AND n.aggregate_id=e.appointment_id
            WHERE e.appointment_id=%s AND e.action='BOOKING_CANCELED'
        """,(appointment,)).fetchone()
        page=rows[:50]
        next_cursor=encode_cursor(page[-1][4],page[-1][0],scope,token) if len(rows)>50 else None
        return {"syntheticOnly":True,"deliveryChannel":"DEV_SINK","realDelivery":False,
                "appointmentId":str(appointment),"workOrderId":str(order),"timeZone":zone,
                "items":[{"bookingId":str(i),"bayCode":bay,"startsAt":start.isoformat(),
                    "endsAt":end.isoformat(),"recordedAt":created.isoformat(),"action":action,
                    "state":"REPLACED" if i!=current else ("CANCELED" if canceled else "CURRENT"),
                    "notificationStatus":status,"attempts":attempts}
                    for i,bay,start,end,created,canceled,action,status,attempts in page],
                "cancellation":({"recordedAt":cancellation[0].isoformat(),
                    "notificationStatus":cancellation[1],"attempts":cancellation[2]}
                    if cancellation else None),"nextCursor":next_cursor}
