"""Closed synthetic appointment creation, never public or production."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
import psycopg
from .auto_org_authority import organization_role_allows

class AppointmentDenied(ValueError):
    pass

class AppointmentConflict(ValueError):
    pass

def create_appointment(conn: psycopg.Connection, token: str, organization: UUID,
                       location: UUID, bay: UUID, link: UUID,
                       starts: datetime, ends: datetime) -> tuple[UUID, UUID, UUID]:
    if (not all(isinstance(x, UUID) for x in (organization,location,bay,link))
            or not all(isinstance(t, datetime) and t.tzinfo is not None
                       and t.utcoffset() is not None for t in (starts,ends))
            or starts <= datetime.now(timezone.utc)
            or ends <= starts or (ends-starts).total_seconds() > 86400):
        raise AppointmentDenied("invalid appointment scope or interval")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization, "INTAKE",
                                        location, for_mutation=True):
            raise AppointmentDenied("organization intake required")
        # Guard current ownership with row locks; no legacy roles are inherited.
        row = conn.execute(
            """SELECT link.vehicle_id FROM auto_vehicle_customer_link link
               JOIN auto_vehicle_location_binding binding
                 ON binding.vehicle_id=link.vehicle_id
               WHERE link.id=%s AND link.verified_at IS NOT NULL
                 AND link.revoked_at IS NULL AND binding.location_id=%s
               FOR SHARE OF link, binding""", (link,location),
        ).fetchone()
        if row is None:
            raise AppointmentDenied("verified scoped vehicle link required")
        if conn.execute(
            """SELECT id FROM auto_work_bay WHERE id=%s AND organization_id=%s
               AND location_id=%s AND disabled_at IS NULL FOR SHARE""",
            (bay,organization,location),
        ).fetchone() is None:
            raise AppointmentDenied("bay unavailable")
        booking, appointment, order = uuid4(),uuid4(),uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_bay_booking
                       (id,organization_id,location_id,bay_id,starts_at,ends_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (booking,organization,location,bay,starts,ends),
                )
                conn.execute(
                    """INSERT INTO auto_appointment
                       (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (appointment,booking,organization,location,row[0],link),
                )
                conn.execute(
                    """INSERT INTO auto_work_order
                       (id,appointment_id,organization_id,location_id)
                       VALUES(%s,%s,%s,%s)""",
                    (order,appointment,organization,location),
                )
        except psycopg.errors.ExclusionViolation as exc:
            raise AppointmentConflict("bay already reserved") from exc
        return appointment,booking,order
