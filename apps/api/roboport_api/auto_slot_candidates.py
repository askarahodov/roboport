"""Read-only synthetic candidate slots; database reservation remains authoritative."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg

from .auto_org_authority import organization_role_allows


class SlotsDenied(ValueError):
    pass


def candidate_slots(conn: psycopg.Connection, token: str, org: UUID,
                    location: UUID, bay: UUID, start: datetime,
                    employee: UUID | None = None) -> list[dict]:
    """At most 47 one-hour candidates with 30-minute steps in a UTC day."""
    if (not all(isinstance(v, UUID) for v in (org, location, bay))
        or (employee is not None and not isinstance(employee, UUID))
        or not isinstance(start, datetime) or start.tzinfo is None
        or start.utcoffset() is None):
        raise SlotsDenied("invalid scope or day")
    day = start.astimezone(timezone.utc)
    if (day.hour, day.minute, day.second, day.microsecond) != (0, 0, 0, 0):
        raise SlotsDenied("UTC midnight required")
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "INTAKE", location,
                                        for_mutation=True):
            raise SlotsDenied("intake authority required")
        found = conn.execute("""
            SELECT 1 FROM auto_work_bay b
            JOIN auto_service_location l ON l.id=b.location_id
            JOIN auto_organization o ON o.id=b.organization_id
            WHERE b.id=%s AND b.organization_id=%s AND b.location_id=%s
              AND b.disabled_at IS NULL AND l.disabled_at IS NULL
              AND o.disabled_at IS NULL AND l.organization_id=o.id
        """, (bay, org, location)).fetchone()
        if found is None:
            raise SlotsDenied("scoped bay unavailable")
        rows = conn.execute("""
            WITH candidates AS (
              SELECT s AS starts_at, s + interval '60 minutes' AS ends_at
              FROM generate_series(
                %s::timestamptz, %s::timestamptz + interval '23 hours',
                interval '30 minutes'
              ) s
            )
            SELECT c.starts_at, c.ends_at FROM candidates c
            WHERE c.starts_at > clock_timestamp()
              AND NOT EXISTS (
                SELECT 1 FROM auto_bay_booking b
                WHERE b.bay_id=%s AND b.organization_id=%s
                  AND b.location_id=%s AND b.canceled_at IS NULL
                  AND b.starts_at<c.ends_at AND b.ends_at>c.starts_at
              )
              AND (%s::uuid IS NULL OR (
                EXISTS (
                  SELECT 1 FROM auto_staff_shift shift
                  WHERE shift.user_id=%s AND shift.organization_id=%s
                    AND shift.location_id=%s AND shift.canceled_at IS NULL
                    AND shift.starts_at<=c.starts_at AND shift.ends_at>=c.ends_at
                )
                AND NOT EXISTS (
                  SELECT 1 FROM auto_employee_reservation er
                  WHERE er.employee_id=%s AND er.canceled_at IS NULL
                    AND er.starts_at<c.ends_at AND er.ends_at>c.starts_at
                )
              ))
            ORDER BY c.starts_at LIMIT 48
        """, (day, day, bay, org, location, employee, employee, org, location,
              employee)).fetchall()
    return [{"startsAt": a.isoformat(), "endsAt": b.isoformat()}
            for a, b in rows]
