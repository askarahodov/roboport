"""Synthetic customer self-service booking; exact verified ownership per mutation."""
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_customer_orders import CustomerOrderDenied
from .auto_customer_booking import bookable_resources, my_appointments
from .auto_appointment_service import (
    create_appointment, reschedule_appointment, cancel_appointment,
    _customer_scope, AppointmentDenied, AppointmentConflict, AppointmentKeyConflict,
    AppointmentDurationInvalid,
)
from .auto_slot_candidates import candidate_slots, SlotsDenied

router = APIRouter(include_in_schema=False)


class CustomerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organizationId: UUID
    locationId: UUID
    customerLinkId: UUID
    bayId: UUID
    startsAt: datetime
    endsAt: datetime
    serviceIds: list[UUID] = Field(default_factory=list, max_length=8)


class CustomerMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organizationId: UUID
    bayId: UUID
    startsAt: datetime
    endsAt: datetime


class CustomerCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    organizationId: UUID


def _deny(request: Request, exc: Exception) -> JSONResponse:
    return _response(request, 401 if str(exc) == "AUTH_REQUIRED" else 403,
                     "AUTH_REQUIRED" if str(exc) == "AUTH_REQUIRED" else "FORBIDDEN")


@router.get("/api/v1/dev/auto/customer/booking/resources")
def get_resources(request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    with psycopg.connect(dsn) as db:
        try:
            items = bookable_resources(db, request.cookies.get("rp_session", ""))
        except CustomerOrderDenied as exc:
            return _deny(request, exc)
    return JSONResponse(content={"items":items,"syntheticOnly":True},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/customer/booking/appointments")
def get_appointments(request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    with psycopg.connect(dsn) as db:
        try:
            items = my_appointments(db, request.cookies.get("rp_session", ""))
        except CustomerOrderDenied as exc:
            return _deny(request, exc)
    return JSONResponse(content={"items":items,"syntheticOnly":True},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/customer/booking/slots")
def get_slots(request: Request, organizationId: str, locationId: str,
              customerLinkId: str, bayId: str, localDay: str):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, link, bay = map(UUID, (organizationId, locationId, customerLinkId, bayId))
        if len(localDay) != 10:
            raise ValueError("invalid local day")
        day = date.fromisoformat(localDay)
        if day.isoformat() != localDay:
            raise ValueError("invalid local day")
        tomorrow = day + timedelta(days=1)
    except (ValueError, TypeError, OverflowError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            with db.transaction():
                token = request.cookies.get("rp_session", "")
                _customer_scope(db, token, org, loc, link)
                row = db.execute(
                    "SELECT timezone_name FROM auto_service_location WHERE id=%s AND organization_id=%s",
                    (loc, org),
                ).fetchone()
                if row is None:
                    raise AppointmentDenied("location unavailable")
                zone = ZoneInfo(row[0])
                start = datetime(day.year,day.month,day.day,tzinfo=zone)
                end = datetime(tomorrow.year,tomorrow.month,tomorrow.day,tzinfo=zone)
                items = candidate_slots(
                    db, token, org, loc, bay, start,
                    day_end=end, customer_link=link,
                )
        except (AppointmentDenied, SlotsDenied, ZoneInfoNotFoundError):
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items":items,"timeZone":row[0],
                                 "guaranteed":False,"syntheticOnly":True},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/customer/booking/services")
def get_booking_services(request: Request, organizationId: str, locationId: str,
                         customerLinkId: str):
    """Только каталог собственного филиала; данные иных клиентов отсутствуют."""
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, link = map(UUID, (organizationId, locationId, customerLinkId))
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            with db.transaction():
                _customer_scope(db, request.cookies.get("rp_session", ""), org, loc, link)
                services = db.execute(
                    """SELECT id,code,title,duration_minutes FROM auto_service_catalog
                       WHERE organization_id=%s AND location_id=%s
                       ORDER BY code,id LIMIT 101""",
                    (org, loc),
                ).fetchall()
        except AppointmentDenied:
            return _response(request, 403, "FORBIDDEN")
    if len(services) > 100:
        return _response(request, 503, "UNAVAILABLE")
    return JSONResponse(content={"items":[
        {"id":str(i),"code":code,"title":title,"durationMinutes":duration}
        for i,code,title,duration in services], "syntheticOnly":True},
        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/customer/booking/appointments")
async def create_customer_booking(request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        body = CustomerCreate.model_validate(await request.json())
        key = UUID(request.headers["Idempotency-Key"])
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError, KeyError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            appointment, booking, work = create_appointment(
                db, request.cookies.get("rp_session", ""), body.organizationId,
                body.locationId, body.bayId, body.customerLinkId,
                body.startsAt, body.endsAt, request_key=key, customer_mode=True,
                service_ids=tuple(body.serviceIds),
            )
        except AppointmentDenied:
            return _response(request, 403, "FORBIDDEN")
        except AppointmentKeyConflict:
            return _response(request, 409, "IDEMPOTENCY_KEY_REUSED")
        except AppointmentDurationInvalid:
            return _response(request, 422, "SERVICES_EXCEED_SLOT")
        except AppointmentConflict:
            return _response(request, 409, "RESOURCE_BUSY")
    return JSONResponse(status_code=201,content={
        "appointmentId":str(appointment),"bookingId":str(booking),"workOrderId":str(work),
    },headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/customer/booking/appointments/{appointment_id}/reschedule")
async def reschedule_customer_booking(appointment_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        ident = UUID(appointment_id)
        body = CustomerMove.model_validate(await request.json())
        key = UUID(request.headers["Idempotency-Key"])
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError, KeyError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            booking = reschedule_appointment(
                db, request.cookies.get("rp_session", ""), body.organizationId,
                ident, body.bayId, body.startsAt, body.endsAt,
                request_key=key, customer_mode=True,
            )
        except AppointmentDenied:
            return _response(request, 403, "FORBIDDEN")
        except AppointmentKeyConflict:
            return _response(request, 409, "IDEMPOTENCY_KEY_REUSED")
        except AppointmentConflict:
            return _response(request, 409, "RESOURCE_BUSY")
    return JSONResponse(content={"status":"RESCHEDULED","bookingId":str(booking)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/customer/booking/appointments/{appointment_id}/cancel")
async def cancel_customer_booking(appointment_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        ident = UUID(appointment_id)
        body = CustomerCancel.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = cancel_appointment(
                db, request.cookies.get("rp_session", ""), body.organizationId,
                ident, customer_mode=True,
            )
        except AppointmentDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status":status},headers={"Cache-Control":"no-store"})
