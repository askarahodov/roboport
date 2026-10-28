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


def cancel_appointment(conn: psycopg.Connection, token: str,
                       organization: UUID, appointment: UUID) -> str:
    """Revoke the reservation, preserving Appointment and WorkOrder history."""
    if not isinstance(organization, UUID) or not isinstance(appointment, UUID):
        raise AppointmentDenied("invalid identifiers")
    from .session_lookup import session_subject
    with conn.transaction():
        # Lock session first, then tenant location and staff grant, then booking.
        actor = session_subject(conn, token, for_mutation=True)
        if actor is None:
            raise AppointmentDenied("session unavailable")
        scoped = conn.execute(
            """SELECT location_id FROM auto_appointment
               WHERE id=%s AND organization_id=%s""",
            (appointment,organization),
        ).fetchone()
        if scoped is None:
            raise AppointmentDenied("appointment unavailable")
        if not organization_role_allows(
            conn, token, organization, "INTAKE", scoped[0], for_mutation=True
        ):
            raise AppointmentDenied("organization intake required")
        # Lock appointment before resolving the latest booking: rescheduling
        # takes the same lock, so a concurrent cancel cannot target stale data.
        conn.execute("SELECT id FROM auto_appointment WHERE id=%s FOR UPDATE", (appointment,))
        current = current_appointment_booking(conn, appointment)
        booking = conn.execute(
            """SELECT b.id,b.canceled_at
               FROM auto_appointment a JOIN auto_bay_booking b ON b.id=%s
               WHERE a.id=%s AND a.organization_id=%s
                 AND b.organization_id=a.organization_id
                 AND b.location_id=a.location_id FOR UPDATE OF b""",
            (current,appointment,organization),
        ).fetchone()
        if booking is None:
            raise AppointmentDenied("booking unavailable")
        if booking[1] is not None:
            return "ALREADY_CANCELED"
        conn.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s", (booking[0],))
        conn.execute(
            """INSERT INTO auto_appointment_actor_action
               (id,appointment_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'CANCELED')""",
            (uuid4(),appointment,organization,actor),
        )
        return "CANCELED"


def current_appointment_booking(conn: psycopg.Connection, appointment: UUID) -> UUID:
    """Caller must first lock the appointment identity row FOR UPDATE."""
    row = conn.execute(
        """SELECT COALESCE(
             (SELECT r.new_booking_id FROM auto_appointment_reschedule r
              WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1),
             a.booking_id)
           FROM auto_appointment a WHERE a.id=%s""", (appointment,),
    ).fetchone()
    if row is None:
        raise AppointmentDenied("appointment unavailable")
    return row[0]


def reschedule_appointment(conn: psycopg.Connection, token: str,
                           organization: UUID, appointment: UUID, bay: UUID,
                           starts: datetime, ends: datetime) -> UUID:
    if (not all(isinstance(x, UUID) for x in (organization,appointment,bay))
            or not all(isinstance(t, datetime) and t.tzinfo is not None
                       and t.utcoffset() is not None for t in (starts,ends))
            or starts <= datetime.now(timezone.utc) or ends <= starts
            or (ends-starts).total_seconds() > 86400):
        raise AppointmentDenied("invalid reschedule")
    with conn.transaction():
        # Serialize all reschedules of one appointment with a row-level lock.
        a = conn.execute(
            """SELECT location_id,customer_link_id,vehicle_id
               FROM auto_appointment
               WHERE id=%s AND organization_id=%s FOR UPDATE""",
            (appointment,organization),
        ).fetchone()
        if a is None or not organization_role_allows(
                conn,token,organization,"INTAKE",a[0],for_mutation=True):
            raise AppointmentDenied("appointment or scope unavailable")
        scope = conn.execute(
            """SELECT 1 FROM auto_vehicle_customer_link link
               JOIN auto_vehicle_location_binding b ON b.vehicle_id=link.vehicle_id
               WHERE link.id=%s AND link.vehicle_id=%s AND link.revoked_at IS NULL
                 AND link.verified_at IS NOT NULL AND b.location_id=%s
               FOR SHARE OF link,b""", (a[1],a[2],a[0]),
        ).fetchone()
        if scope is None:
            raise AppointmentDenied("verified vehicle link unavailable")
        old = current_appointment_booking(conn,appointment)
        previous = conn.execute(
            """SELECT id,canceled_at FROM auto_bay_booking
               WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE""",
            (old,organization,a[0]),
        ).fetchone()
        if previous is None or previous[1] is not None:
            raise AppointmentDenied("appointment already canceled")
        # The INSERT trigger also locks enabled bay/location/organization rows.
        fresh = uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_bay_booking
                       (id,organization_id,location_id,bay_id,starts_at,ends_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (fresh,organization,a[0],bay,starts,ends),
                )
                from .session_lookup import session_subject
                actor = session_subject(conn,token,for_mutation=True)
                if actor is None:
                    raise AppointmentDenied("session unavailable")
                conn.execute(
                    """INSERT INTO auto_appointment_reschedule
                       (id,appointment_id,organization_id,old_booking_id,
                        new_booking_id,actor_id)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),appointment,organization,old,fresh,actor),
                )
                conn.execute(
                    "UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
                    (old,),
                )
        except psycopg.errors.ExclusionViolation as exc:
            raise AppointmentConflict("bay already reserved") from exc
        return fresh
