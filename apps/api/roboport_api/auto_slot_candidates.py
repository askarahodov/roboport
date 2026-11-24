"""Read-only synthetic candidate slots; database reservation remains authoritative."""
from datetime import datetime, timedelta, timezone
from uuid import UUID

import psycopg

from .auto_org_authority import organization_role_allows


class SlotsDenied(ValueError):
    pass


def parse_slot_services(values: list[str]) -> tuple[UUID, ...]:
    """До восьми уникальных UUID из повторяемых query-полей serviceId."""
    if len(values) > 8:
        raise ValueError("too many selected services")
    ids = tuple(UUID(value) for value in values)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate selected service")
    return ids


def candidate_slots(conn: psycopg.Connection, token: str, org: UUID,
                    location: UUID, bay: UUID, start: datetime,
                    employee: UUID | None = None,
                    appointment: UUID | None = None,
                    day_end: datetime | None = None,
                    customer_link: UUID | None = None,
                    service_ids: tuple[UUID, ...] = (),
                    *, clear_employee: bool = False, complex_id: UUID | None = None) -> list[dict]:
    """Кандидаты на UTC либо локальные сутки; бронь не гарантируется."""
    if (not all(isinstance(v, UUID) for v in (org, location, bay))
        or (employee is not None and not isinstance(employee, UUID))
        or (appointment is not None and not isinstance(appointment, UUID))
        or (customer_link is not None and not isinstance(customer_link, UUID))
        or type(clear_employee) is not bool
        or (clear_employee and (employee is not None or appointment is None))
        or (customer_link is not None and (employee is not None or clear_employee or complex_id is not None))
        or (complex_id is not None and (not isinstance(complex_id, UUID) or appointment is not None or service_ids))
        or type(service_ids) is not tuple or len(service_ids) > 8
        or any(type(item) is not UUID for item in service_ids)
        or len(set(service_ids)) != len(service_ids)
        or not isinstance(start, datetime) or start.tzinfo is None
        or start.utcoffset() is None
        or (day_end is not None and (not isinstance(day_end, datetime)
            or day_end.tzinfo is None or day_end.utcoffset() is None))):
        raise SlotsDenied("invalid scope or day")
    day = start.astimezone(timezone.utc)
    if day_end is None:
        if (day.hour, day.minute, day.second, day.microsecond) != (0, 0, 0, 0):
            raise SlotsDenied("UTC midnight required")
        end = day + timedelta(days=1)
    else:
        end = day_end.astimezone(timezone.utc)
        if not timedelta(hours=23) <= end - day <= timedelta(hours=25):
            raise SlotsDenied("invalid local day duration")
    with conn.transaction():
        if customer_link is not None:
            from .auto_appointment_service import _customer_scope, AppointmentDenied
            try:
                _customer_scope(conn, token, org, location, customer_link)
            except AppointmentDenied as exc:
                raise SlotsDenied("customer scope required") from exc
        elif not organization_role_allows(conn, token, org, "INTAKE", location,
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
        # При переносе сервер сам определяет работы и сохраняемого специалиста.
        # Переданные клиентом идентификаторы не заменяют состав существующего ЗН.
        old_booking = None
        duration_minutes = 60
        if appointment is not None:
            scoped = conn.execute("""
                SELECT cur.id,w.id,er.employee_id,
                       EXTRACT(EPOCH FROM cur.ends_at-cur.starts_at)
                FROM auto_appointment a
                JOIN auto_work_order w ON w.appointment_id=a.id
                  AND w.organization_id=a.organization_id AND w.location_id=a.location_id
                JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
                  AND proof.vehicle_id=a.vehicle_id AND proof.verified_at IS NOT NULL
                  AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
                JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
                  AND binding.location_id=a.location_id
                JOIN auto_bay_booking cur ON cur.id=COALESCE((
                    SELECT r.new_booking_id FROM auto_appointment_reschedule r
                    WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
                  ),a.booking_id)
                  AND cur.organization_id=a.organization_id
                  AND cur.location_id=a.location_id AND cur.canceled_at IS NULL
                LEFT JOIN auto_employee_reservation er ON er.booking_id=cur.id
                  AND er.canceled_at IS NULL
                WHERE a.id=%s AND a.organization_id=%s AND a.location_id=%s
                  AND (%s::uuid IS NULL OR a.customer_link_id=%s::uuid)
                FOR SHARE OF a,w,proof,binding,cur
            """, (appointment,org,location,customer_link,customer_link)).fetchone()
            if scoped is None:
                raise SlotsDenied("scoped active appointment required")
            if customer_link is not None:
                from .auto_appointment_service import _customer_order_pristine, AppointmentDenied
                try:
                    _customer_order_pristine(conn,appointment)
                except AppointmentDenied as exc:
                    raise SlotsDenied("appointment already in progress") from exc
            old_booking, order, retained, original_seconds = scoped
            employee = None if clear_employee else employee or retained
            chosen=conn.execute("""
                SELECT cat.id,cat.duration_minutes FROM auto_work_order_service sel
                JOIN auto_service_catalog cat ON cat.id=sel.service_id
                  AND cat.organization_id=sel.organization_id AND cat.location_id=sel.location_id
                WHERE sel.work_order_id=%s AND sel.organization_id=%s AND sel.location_id=%s
                ORDER BY cat.id FOR SHARE OF cat
            """,(order,org,location)).fetchall()
            if service_ids and set(service_ids)!={row[0] for row in chosen}:
                raise SlotsDenied("appointment services mismatch")
            # Без выбранных работ сохраняется длительность исходной брони.
            # Округляем вверх до минуты, чтобы не обещать укороченный интервал.
            duration_minutes=sum(row[1] for row in chosen) if chosen else int((original_seconds+59)//60)
        elif complex_id is not None:
            from .auto_booking_directory import complex_composition, BookingDirectoryDenied
            try:
                duration_minutes,_=complex_composition(conn,token,org,location,complex_id)
            except BookingDirectoryDenied as exc:
                raise SlotsDenied("complex unavailable") from exc
        elif service_ids:
            services=conn.execute("""
                SELECT id,duration_minutes FROM auto_service_catalog
                WHERE organization_id=%s AND location_id=%s
                  AND id=ANY(%s::uuid[]) ORDER BY id FOR SHARE
            """,(org,location,list(service_ids))).fetchall()
            if len(services)!=len(service_ids):
                raise SlotsDenied("service catalog scope unavailable")
            duration_minutes=sum(row[1] for row in services)
        if not 1<=duration_minutes<=1440:
            raise SlotsDenied("selected duration unsupported")
        rows = conn.execute("""
            WITH candidates AS (
              SELECT s AS starts_at,
                     s + (%s * interval '1 minute') AS ends_at
              FROM generate_series(
                %s::timestamptz,
                %s::timestamptz - (%s * interval '1 minute'),
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
              AND NOT EXISTS (
                SELECT 1 FROM auto_location_opening_policy policy
                JOIN auto_service_location loc ON loc.id=policy.location_id
                WHERE policy.organization_id=%s AND policy.location_id=%s
                  AND NOT auto_location_hours_allow(
                    loc.timezone_name,policy.windows,policy.closed_dates,
                    c.starts_at,c.ends_at
                  )
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
                    AND (%s::uuid IS NULL OR er.booking_id<>%s::uuid)
                    AND er.starts_at<c.ends_at AND er.ends_at>c.starts_at
                )
              ))
            ORDER BY c.starts_at LIMIT 50
        """, (duration_minutes,day,end,duration_minutes,
              bay,org,location,org,location,employee,employee,org,location,
              employee,old_booking,old_booking)).fetchall()
    return [{"startsAt": a.isoformat(), "endsAt": b.isoformat()}
            for a, b in rows]
