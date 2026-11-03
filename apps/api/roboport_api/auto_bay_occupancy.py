"""Synthetic-only manager dashboard: current bay occupancy by local calendar day.

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
