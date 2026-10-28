"""Bounded synthetic staff calendar; no customer personal data in responses."""
from datetime import datetime, timedelta
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows

class CalendarDenied(ValueError):
    pass

def calendar_items(conn: psycopg.Connection, token: str, org: UUID, location: UUID,
                   start: datetime, end: datetime) -> list[dict]:
    if (not isinstance(org, UUID) or not isinstance(location, UUID)
        or not all(isinstance(t,datetime) and t.tzinfo is not None and
                   t.utcoffset() is not None for t in (start,end))
        or end<=start or end-start>timedelta(days=7)):
        raise CalendarDenied("invalid calendar window")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"INTAKE",location,
                                       for_mutation=True):
            raise CalendarDenied("intake authority required")
        rows=conn.execute("""
          SELECT a.id, b.id,b.bay_id,b.starts_at,b.ends_at,w.id
          FROM auto_appointment a
          JOIN LATERAL (
            SELECT COALESCE(
              (SELECT r.new_booking_id FROM auto_appointment_reschedule r
               WHERE r.appointment_id=a.id
               ORDER BY r.created_at DESC,r.id DESC LIMIT 1),a.booking_id
            ) AS current_id
          ) resolved ON TRUE
          JOIN auto_bay_booking b ON b.id=resolved.current_id
          JOIN auto_work_order w ON w.appointment_id=a.id
          WHERE a.organization_id=%s AND a.location_id=%s
            AND b.organization_id=a.organization_id
            AND b.location_id=a.location_id AND b.canceled_at IS NULL
            AND b.starts_at < %s AND b.ends_at > %s
          ORDER BY b.starts_at,a.id LIMIT 100
        """,(org,location,end,start)).fetchall()
        return [{"appointmentId":str(a),"bookingId":str(b),"bayId":str(bay),
                 "startsAt":s.isoformat(),"endsAt":e.isoformat(),
                 "workOrderId":str(w)} for a,b,bay,s,e,w in rows]
