"""Closed synthetic worker shift coverage check; not a booking authorization."""
from datetime import datetime, timezone, timedelta
from uuid import UUID

import psycopg


def shift_covers_interval(conn: psycopg.Connection, organization: UUID,
                          location: UUID, employee: UUID,
                          starts: datetime, ends: datetime) -> bool:
    """True only if one active scoped shift covers the entire interval.

    Caller owns authorization and any appointment/employee locking; never
    interpret this read-only helper as a reservation guarantee.
    """
    if (not all(isinstance(value, UUID) for value in (organization, location, employee))
        or not all(isinstance(value, datetime) and value.tzinfo is not None
                   and value.utcoffset() is not None for value in (starts, ends))
        or starts <= datetime.now(timezone.utc)
        or ends <= starts or ends - starts > timedelta(hours=24)):
        return False
    row = conn.execute("""
        SELECT 1 FROM auto_staff_shift s
        JOIN auto_service_location l ON l.id=s.location_id
        JOIN auto_organization o ON o.id=s.organization_id
        WHERE s.organization_id=%s AND s.location_id=%s AND s.user_id=%s
          AND l.organization_id=s.organization_id
          AND s.canceled_at IS NULL
          AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          AND s.starts_at<=%s AND s.ends_at>=%s
        LIMIT 1
    """, (organization, location, employee, starts, ends)).fetchone()
    return row is not None
