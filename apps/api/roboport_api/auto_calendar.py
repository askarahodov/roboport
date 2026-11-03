"""Bounded synthetic staff calendar; no customer personal data in responses."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID
import re
import psycopg
from .auto_org_authority import organization_role_allows

class CalendarDenied(ValueError):
    pass


class CalendarInvalid(ValueError):
    """Invalid calendar window/cursor; not an authorization failure."""
    pass

def local_day_window(conn: psycopg.Connection, token: str, org: UUID,
                     location: UUID, local_day: str) -> tuple[datetime, datetime, str]:
    """Локальные сутки филиала; их длительность может быть 23/24/25 часов."""
    if (not isinstance(local_day, str) or
        re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", local_day) is None):
        raise CalendarInvalid("invalid local day")
    try:
        parsed = date.fromisoformat(local_day)
        if parsed.isoformat() != local_day:
            raise ValueError("noncanonical local day")
    except ValueError as exc:
        raise CalendarInvalid("invalid local day") from exc
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "INTAKE", location,
                                        for_mutation=True):
            raise CalendarDenied("intake authority required")
        row = conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL""",
            (location, org),
        ).fetchone()
        if row is None:
            raise CalendarDenied("location unavailable")
        try:
            zone = ZoneInfo(row[0])
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CalendarDenied("location timezone unavailable") from exc
        try:
            start = datetime(parsed.year, parsed.month, parsed.day, tzinfo=zone)
            tomorrow = parsed + timedelta(days=1)
            end = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=zone)
        except (OverflowError, ValueError) as exc:
            raise CalendarInvalid("local day outside supported range") from exc
        return start, end, row[0]


def calendar_items(conn: psycopg.Connection, token: str, org: UUID, location: UUID,
                   start: datetime, end: datetime, after_start: datetime | None = None,
                   after_appointment: UUID | None = None) -> list[dict]:
    if (not isinstance(org, UUID) or not isinstance(location, UUID)
        or not all(isinstance(t,datetime) and t.tzinfo is not None and
                   t.utcoffset() is not None for t in (start,end))
        or end<=start or end-start>timedelta(days=7)
        or (after_start is None) != (after_appointment is None)
        or (after_start is not None and
            (not isinstance(after_start,datetime) or after_start.tzinfo is None
             or after_start.utcoffset() is None or not isinstance(after_appointment,UUID)))):
        raise CalendarInvalid("invalid calendar window")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"INTAKE",location,
                                       for_mutation=True):
            raise CalendarDenied("intake authority required")
        rows=conn.execute("""
          SELECT a.id, b.id,b.bay_id,b.starts_at,b.ends_at,w.id,er.employee_id
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
          LEFT JOIN auto_employee_reservation er ON er.booking_id=b.id
            AND er.organization_id=a.organization_id
            AND er.location_id=a.location_id AND er.canceled_at IS NULL
          WHERE a.organization_id=%s AND a.location_id=%s
            AND b.organization_id=a.organization_id
            AND b.location_id=a.location_id AND b.canceled_at IS NULL
            AND b.starts_at < %s AND b.ends_at > %s
            AND (%s::timestamptz IS NULL OR
                 (b.starts_at,a.id)>(%s::timestamptz,%s::uuid))
          ORDER BY b.starts_at,a.id LIMIT 101
        """,(org,location,end,start,after_start,after_start,after_appointment)).fetchall()
        return [{"appointmentId":str(a),"bookingId":str(b),"bayId":str(bay),
                 "startsAt":s.isoformat(),"endsAt":e.isoformat(),
                 "workOrderId":str(w),"employeeId":str(employee) if employee else None}
                for a,b,bay,s,e,w,employee in rows]
