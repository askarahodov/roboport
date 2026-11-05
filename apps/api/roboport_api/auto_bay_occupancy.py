"""Synthetic-only manager dashboard: active bay and staff assignment aggregates.

No customer, VIN, appointment, employee, price or financial data is exposed.
A cancelled/rescheduled historical booking does not inflate live occupancy.
"""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auto_vehicle_claim_http import _request_dsn, _response

from .auto_org_authority import organization_role_allows


router = APIRouter(include_in_schema=False)


class OccupancyDenied(ValueError):
    pass


class OccupancyInvalid(ValueError):
    pass


def _day(value: str) -> date:
    if (type(value) is not str or len(value) != 10
            or value[4] != "-" or value[7] != "-"):
        raise OccupancyInvalid("invalid local date")
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except ValueError:
        raise OccupancyInvalid("invalid local date") from None


def bay_occupancy(conn: psycopg.Connection, token: str, *,
                  organization_id: UUID, location_id: UUID,
                  from_day: str, to_day: str) -> dict:
    if (type(organization_id) is not UUID or type(location_id) is not UUID):
        raise OccupancyInvalid("invalid scope")
    first, last = _day(from_day), _day(to_day)
    length = (last-first).days + 1
    if not 1 <= length <= 31 or last >= date.max:
        raise OccupancyInvalid("report period must be 1..31 days")
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "MANAGER", location_id,
            for_mutation=True,
        ):
            raise OccupancyDenied("manager role required")
        row = conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",
            (location_id, organization_id),
        ).fetchone()
        if row is None:
            raise OccupancyDenied("location unavailable")
        try:
            zone = ZoneInfo(row[0])
        except (ZoneInfoNotFoundError, ValueError):
            raise OccupancyDenied("timezone unavailable") from None
        bay_count = conn.execute(
            """SELECT count(*) FROM auto_work_bay
               WHERE organization_id=%s AND location_id=%s
                 AND disabled_at IS NULL""",
            (organization_id, location_id),
        ).fetchone()[0]

        # Current appointment identity is authoritative, not all historical
        # auto_bay_booking rows. A previous reschedule/cancellation is not
        # double-counted. Query is bounded to 31 dates, aggregate only.
        items = []
        for index in range(length):
            current_day = first + timedelta(days=index)
            next_day = current_day + timedelta(days=1)
            start = datetime(
                current_day.year, current_day.month, current_day.day,tzinfo=zone,
            )
            end = datetime(
                next_day.year, next_day.month, next_day.day,tzinfo=zone,
            )
            day_seconds = int((end.astimezone(timezone.utc) -
                               start.astimezone(timezone.utc)).total_seconds())
            count, seconds = conn.execute(
                """SELECT count(*)::bigint,
                          COALESCE(SUM(EXTRACT(EPOCH FROM
                            LEAST(b.ends_at,%s::timestamptz) -
                            GREATEST(b.starts_at,%s::timestamptz)
                          )),0)::bigint
                   FROM auto_appointment a
                   JOIN LATERAL (
                      SELECT COALESCE(
                        (SELECT r.new_booking_id
                         FROM auto_appointment_reschedule r
                         WHERE r.appointment_id=a.id
                         ORDER BY r.created_at DESC,r.id DESC LIMIT 1),
                        a.booking_id
                      ) AS current_id
                   ) latest ON TRUE
                   JOIN auto_bay_booking b ON b.id=latest.current_id
                   JOIN auto_work_bay active_bay ON active_bay.id=b.bay_id
                     AND active_bay.organization_id=a.organization_id
                     AND active_bay.location_id=a.location_id
                     AND active_bay.disabled_at IS NULL
                   WHERE a.organization_id=%s AND a.location_id=%s
                     AND b.organization_id=a.organization_id
                     AND b.location_id=a.location_id
                     AND b.canceled_at IS NULL
                     AND b.starts_at < %s AND b.ends_at > %s""",
                (end, start, organization_id, location_id, end, start),
            ).fetchone()

            # Active shifts are themselves immutable time ranges with a
            # non-overlap constraint per employee. Only aggregate capacity;
            # never return user IDs or individual shifts.
            scheduled_count, shift_seconds = conn.execute(
                """SELECT COUNT(DISTINCT sh.user_id)::bigint,
                          COALESCE(SUM(EXTRACT(EPOCH FROM
                             LEAST(sh.ends_at,%s::timestamptz) -
                             GREATEST(sh.starts_at,%s::timestamptz)
                          )),0)::bigint
                   FROM auto_staff_shift sh
                   WHERE sh.organization_id=%s AND sh.location_id=%s
                     AND sh.canceled_at IS NULL
                     AND sh.starts_at < %s AND sh.ends_at > %s""",
                (end,start,organization_id,location_id,end,start),
            ).fetchone()
            # Only current appointment bookings count. A stale employee
            # reservation on a superseded booking cannot inflate the report.
            # An assignment whose shift was revoked is excluded; it cannot
            # be reported as reserved capacity of an active shift.
            assigned_count, assigned_seconds = conn.execute(
                """SELECT COUNT(*)::bigint,
                          COALESCE(SUM(EXTRACT(EPOCH FROM
                             LEAST(er.ends_at,%s::timestamptz) -
                             GREATEST(er.starts_at,%s::timestamptz)
                          )),0)::bigint
                   FROM auto_employee_reservation er
                   JOIN auto_bay_booking b ON b.id=er.booking_id
                     AND b.organization_id=er.organization_id
                     AND b.location_id=er.location_id
                     AND b.canceled_at IS NULL
                   JOIN auto_work_bay bay ON bay.id=b.bay_id
                     AND bay.organization_id=er.organization_id
                     AND bay.location_id=er.location_id
                     AND bay.disabled_at IS NULL
                   JOIN auto_appointment a ON a.organization_id=b.organization_id
                     AND a.location_id=b.location_id
                   JOIN LATERAL (
                     SELECT COALESCE(
                       (SELECT r.new_booking_id FROM auto_appointment_reschedule r
                        WHERE r.appointment_id=a.id
                        ORDER BY r.created_at DESC,r.id DESC LIMIT 1),
                       a.booking_id
                     ) AS current_id
                   ) latest ON latest.current_id=b.id
                   WHERE er.organization_id=%s AND er.location_id=%s
                     AND er.canceled_at IS NULL
                     AND er.starts_at < %s AND er.ends_at > %s
                     AND EXISTS(
                       SELECT 1 FROM auto_staff_shift sh
                       WHERE sh.organization_id=er.organization_id
                         AND sh.location_id=er.location_id
                         AND sh.user_id=er.employee_id
                         AND sh.canceled_at IS NULL
                         AND sh.starts_at<=er.starts_at
                         AND sh.ends_at>=er.ends_at
                     )""",
                (end,start,organization_id,location_id,end,start),
            ).fetchone()
            # Immutable labor ledger, attributed to the LOCAL DAY WHEN
            # RECORDED, not the booking date or the promised duration.
            # A finish event belongs to its own completion date. Neither
            # metric is attendance, time clock, billable hours or payroll.
            labor_count, labor_minutes = conn.execute(
                """SELECT COUNT(*)::bigint, COALESCE(SUM(l.actual_minutes),0)::bigint
                   FROM auto_work_order_labor_entry l
                   JOIN auto_work_order_execution e ON e.id=l.execution_id
                   JOIN auto_work_order w ON w.id=e.work_order_id
                   WHERE e.organization_id=%s AND e.location_id=%s
                     AND w.organization_id=e.organization_id
                     AND w.location_id=e.location_id
                     AND l.created_at >= %s AND l.created_at < %s""",
                (organization_id, location_id, start, end),
            ).fetchone()
            finished_count = conn.execute(
                """SELECT COUNT(*)::bigint
                   FROM auto_work_order_execution_finish f
                   JOIN auto_work_order_execution e ON e.id=f.execution_id
                   JOIN auto_work_order w ON w.id=e.work_order_id
                   WHERE e.organization_id=%s AND e.location_id=%s
                     AND w.organization_id=e.organization_id
                     AND w.location_id=e.location_id
                     AND f.created_at >= %s AND f.created_at < %s""",
                (organization_id, location_id, start, end),
            ).fetchone()[0]
            # Appointment lifecycle: event timestamps are authoritative.
            # A move is ONE event, never a new created appointment.
            created_count, canceled_count = conn.execute(
                """SELECT COUNT(*) FILTER (WHERE ev.action='CREATED')::bigint,
                          COUNT(*) FILTER (WHERE ev.action='BOOKING_CANCELED')::bigint
                   FROM auto_appointment_event ev
                   JOIN auto_appointment a ON a.id=ev.appointment_id
                   WHERE a.organization_id=%s AND a.location_id=%s
                     AND ev.recorded_at >= %s AND ev.recorded_at < %s""",
                (organization_id, location_id, start, end),
            ).fetchone()
            moved_count = conn.execute(
                """SELECT COUNT(*)::bigint FROM auto_appointment_reschedule r
                   JOIN auto_appointment a ON a.id=r.appointment_id
                   WHERE a.organization_id=%s AND a.location_id=%s
                     AND r.organization_id=a.organization_id
                     AND r.created_at >= %s AND r.created_at < %s""",
                (organization_id, location_id, start, end),
            ).fetchone()[0]
            # Repeat BOOKING, not a completed physical visit. Compare the
            # current appointment against ALL prior appointments of the same
            # synthetic customer in THIS branch, including canceled ones.
            # Do not expose the underlying customer identifiers.
            starts_count, customers_count, repeats_count = conn.execute(
                """SELECT COUNT(*)::bigint,
                          COUNT(DISTINCT link.customer_id)::bigint,
                          COUNT(DISTINCT link.customer_id) FILTER (
                            WHERE EXISTS (
                              SELECT 1 FROM auto_appointment previous
                              JOIN auto_vehicle_customer_link prev_link
                                ON prev_link.id=previous.customer_link_id
                              WHERE previous.organization_id=a.organization_id
                                AND previous.location_id=a.location_id
                                AND prev_link.customer_id=link.customer_id
                                AND (previous.created_at,previous.id)<
                                    (a.created_at,a.id)
                            )
                          )::bigint
                   FROM auto_appointment a
                   JOIN auto_vehicle_customer_link link
                     ON link.id=a.customer_link_id
                   JOIN LATERAL (
                     SELECT COALESCE(
                        (SELECT r.new_booking_id FROM auto_appointment_reschedule r
                         WHERE r.appointment_id=a.id
                         ORDER BY r.created_at DESC,r.id DESC LIMIT 1),
                        a.booking_id
                     ) AS booking_id
                   ) latest ON TRUE
                   JOIN auto_bay_booking b ON b.id=latest.booking_id
                   JOIN auto_work_bay bay ON bay.id=b.bay_id
                     AND bay.organization_id=a.organization_id
                     AND bay.location_id=a.location_id
                     AND bay.disabled_at IS NULL
                   WHERE a.organization_id=%s AND a.location_id=%s
                     AND b.organization_id=a.organization_id
                     AND b.location_id=a.location_id
                     AND b.canceled_at IS NULL
                     AND b.starts_at >= %s AND b.starts_at < %s""",
                (organization_id, location_id, start, end),
            ).fetchone()
            capacity = bay_count * day_seconds
            # Never claim this is operating-hours utilization: denominator
            # includes the WHOLE local calendar day, including closed hours.
            items.append({
                "localDay": current_day.isoformat(),
                "daySeconds": day_seconds,
                "activeAppointments": count,
                "occupiedSeconds": seconds,
                "calendarCapacitySeconds": capacity,
                "occupancyBasisPoints": (seconds*10000//capacity if capacity else None),
                "scheduledEmployees": scheduled_count,
                "scheduledShiftSeconds": shift_seconds,
                "activeAssignments": assigned_count,
                "assignedSeconds": assigned_seconds,
                "assignmentBasisPoints": (
                    assigned_seconds*10000//shift_seconds if shift_seconds else None
                ),
                "recordedLaborEntries": labor_count,
                "recordedLaborMinutes": labor_minutes,
                "finishedExecutions": finished_count,
                "createdAppointments": created_count,
                "canceledAppointments": canceled_count,
                "rescheduleEvents": moved_count,
                "scheduledStarts": starts_count,
                "scheduledCustomers": customers_count,
                "repeatBookingCustomers": repeats_count,
            })
        return {
            "items":items, "timeZone":row[0], "bayCount":bay_count,
            "basis":"CURRENT_BOOKINGS_CALENDAR_DAY",
            "syntheticOnly":True,
        }


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/bay-occupancy")
def get_bay_occupancy(org_id: str, loc_id: str, request: Request,
                      fromDay: str, toDay: str):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org = UUID(org_id)
        loc = UUID(loc_id)
        with psycopg.connect(dsn) as db:
            result = bay_occupancy(
                db,request.cookies.get("rp_session",""),
                organization_id=org,location_id=loc,
                from_day=fromDay,to_day=toDay,
            )
    except (ValueError, OccupancyInvalid) as exc:
        if isinstance(exc, OccupancyDenied):
            return _response(request,403,"FORBIDDEN")
        return _response(request,422,"INVALID_INPUT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
