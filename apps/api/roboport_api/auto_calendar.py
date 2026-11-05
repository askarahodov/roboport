"""Bounded synthetic staff calendar; no customer personal data in responses."""
import csv
from io import StringIO
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
        re.fullmatch(r"\d{4}-\d{2}-\d{2}", local_day) is None):
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


def calendar_week_summary(conn: psycopg.Connection, token: str, org: UUID,
                          location: UUID, first_day: str) -> dict:
    """Seven branch-local days, including DST, with live INTAKE authority.

    No appointment/customer/employee identifiers leave the aggregate. Counts
    represent CURRENT non-canceled bookings overlapping each local day; a
    booking spanning midnight may be counted on two separate days.
    """
    with conn.transaction():
        start, _, zone_name = local_day_window(conn, token, org, location, first_day)
        zone = ZoneInfo(zone_name)
        try:
            days = [start.date() + timedelta(days=i) for i in range(8)]
        except OverflowError as exc:
            raise CalendarInvalid("week exceeds date range") from exc
        bounds = [datetime(day.year, day.month, day.day, tzinfo=zone) for day in days]
        starts, ends = bounds[:-1], bounds[1:]
        rows = conn.execute(
            """WITH days AS (
                 SELECT start_at, end_at, ord
                 FROM unnest(%s::timestamptz[], %s::timestamptz[])
                   WITH ORDINALITY AS d(start_at,end_at,ord)
               )
               SELECT days.ord, COUNT(DISTINCT b.id)::bigint,
                      COUNT(DISTINCT b.bay_id)::bigint
               FROM days
               LEFT JOIN auto_appointment a
                 ON a.organization_id=%s AND a.location_id=%s
               LEFT JOIN LATERAL (
                 SELECT COALESCE(
                   (SELECT r.new_booking_id FROM auto_appointment_reschedule r
                    WHERE r.appointment_id=a.id
                    ORDER BY r.created_at DESC,r.id DESC LIMIT 1),
                   a.booking_id
                 ) AS current_id
               ) resolved ON a.id IS NOT NULL
               LEFT JOIN auto_bay_booking b ON b.id=resolved.current_id
                 AND b.organization_id=a.organization_id
                 AND b.location_id=a.location_id
                 AND b.canceled_at IS NULL
                 AND b.starts_at < days.end_at AND b.ends_at > days.start_at
               GROUP BY days.ord ORDER BY days.ord""",
            (starts, ends, org, location),
        ).fetchall()
        if len(rows) != 7 or [int(row[0]) for row in rows] != list(range(1, 8)):
            raise CalendarInvalid("incomplete week aggregation")
        return {
            "syntheticOnly": True, "timeZone": zone_name,
            "fromDay": first_day,
            "items": [
                {"localDay": days[i].isoformat(),
                 "appointments": int(rows[i][1]),
                 "occupiedBays": int(rows[i][2])}
                for i in range(7)
            ],
        }


def calendar_week_csv(report: dict) -> str:
    """Fixed-schema CSV with only branch-local dates and aggregate integers."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Дата филиала", "Действующие записи", "Занятые посты"))
    for row in report["items"]:
        writer.writerow((row["localDay"], row["appointments"], row["occupiedBays"]))
    return "\ufeff" + output.getvalue()
