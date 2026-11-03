"""Synthetic loopback-only appointment create and cancellation endpoints."""
from datetime import datetime
from uuid import UUID
from typing import Literal
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_appointment_service import (
    create_appointment, cancel_appointment, reschedule_appointment, AppointmentDenied, AppointmentConflict, AppointmentKeyConflict, AppointmentDurationInvalid, AppointmentStockUnavailable, AppointmentHoursClosed,
)
router=APIRouter(include_in_schema=False)

class BookingMaterialItem(BaseModel):
    model_config=ConfigDict(extra="forbid")
    skuId: UUID
    kind: Literal["PART","CONSUMABLE"]
    quantity: int = Field(gt=0, le=1000000000, strict=True)


class CreateAppointment(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    bayId: UUID
    customerLinkId: UUID
    employeeId: UUID | None = None
    startsAt: datetime
    endsAt: datetime
    serviceIds: list[UUID] = Field(default_factory=list, max_length=8)
    materialItems: list[BookingMaterialItem] = Field(default_factory=list, max_length=8)

@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/booking-services")
def intake_booking_services(org_id: str, loc_id: str, request: Request):
    """Каталог филиала для INTAKE: без назначения роли MANAGER."""
    from .auto_org_authority import organization_role_allows
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        with db.transaction():
            if not organization_role_allows(
                db, request.cookies.get("rp_session", ""), org,
                "INTAKE", loc, for_mutation=True,
            ):
                return _response(request, 403, "FORBIDDEN")
            rows = db.execute(
                """SELECT id,code,title,duration_minutes FROM auto_service_catalog
                   WHERE organization_id=%s AND location_id=%s
                   ORDER BY code,id LIMIT 101""",
                (org, loc),
            ).fetchall()
    if len(rows) > 100:
        return _response(request, 503, "UNAVAILABLE")
    return JSONResponse(
        content={"items":[
            {"id":str(i),"code":code,"title":title,"durationMinutes":minutes}
            for i,code,title,minutes in rows
        ],"syntheticOnly":True},
        headers={"Cache-Control":"no-store"},
    )


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/booking-stock")
def intake_booking_stock(org_id: str, loc_id: str, request: Request):
    """Только INTAKE+WAREHOUSE: свободный синтетический остаток без PII."""
    from .auto_org_authority import organization_role_allows
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        with db.transaction():
            token=request.cookies.get("rp_session","")
            if not (organization_role_allows(db,token,org,"INTAKE",loc,for_mutation=True)
                    and organization_role_allows(db,token,org,"WAREHOUSE",loc,for_mutation=True)):
                return _response(request,403,"FORBIDDEN")
            items=db.execute(
                """SELECT s.id,s.article,b.on_hand-b.reserved AS available
                   FROM auto_stock_sku s
                   JOIN auto_stock_balance b ON b.sku_id=s.id
                   WHERE s.organization_id=%s AND s.location_id=%s
                   ORDER BY s.article,s.id LIMIT 101""",
                (org,loc),
            ).fetchall()
    if len(items)>100:
        return _response(request,503,"UNAVAILABLE")
    return JSONResponse(
        content={"items":[
            {"skuId":str(ident),"article":article,"available":max(0,available)}
            for ident,article,available in items
        ],"syntheticOnly":True},
        headers={"Cache-Control":"no-store"},
    )


@router.post("/api/v1/dev/auto/organizations/{org_id}/appointments")
async def create(org_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        body=CreateAppointment.model_validate(await request.json())
        idempotency_header=request.headers.get('Idempotency-Key')
        request_key=UUID(idempotency_header) if idempotency_header is not None else None
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            appointment,booking,work_order=create_appointment(
                db,request.cookies.get("rp_session",""),org,body.locationId,
                body.bayId,body.customerLinkId,body.startsAt,body.endsAt,body.employeeId,
                request_key, service_ids=tuple(body.serviceIds),
                material_items=tuple(
                    (item.skuId,item.kind,item.quantity)
                    for item in body.materialItems
                ),
            )
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
        except AppointmentKeyConflict:
            return _response(request,409,"IDEMPOTENCY_KEY_REUSED")
        except AppointmentDurationInvalid:
            return _response(request,422,"SERVICES_EXCEED_SLOT")
        except AppointmentStockUnavailable:
            return _response(request,409,"STOCK_UNAVAILABLE")
        except AppointmentHoursClosed:
            return _response(request,409,"LOCATION_CLOSED")
        except AppointmentConflict:
            return _response(request,409,"RESOURCE_BUSY")
    return JSONResponse(status_code=201,content={
        "appointmentId":str(appointment),"bookingId":str(booking),
        "workOrderId":str(work_order)},headers={"Cache-Control":"no-store"})

@router.post("/api/v1/dev/auto/organizations/{org_id}/appointments/{appointment_id}/cancel")
def cancel(org_id: str, appointment_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,ident=UUID(org_id),UUID(appointment_id)
        header=request.headers.get("Idempotency-Key")
        request_key=UUID(header) if header is not None else None
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            outcome=cancel_appointment(db,request.cookies.get("rp_session",""),org,ident,
                                       request_key=request_key)
        except AppointmentKeyConflict:
            return _response(request,409,"IDEMPOTENCY_KEY_REUSED")
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
        except AppointmentConflict:
            return _response(request,409,"STOCK_RECONCILIATION_REQUIRED")
    return JSONResponse(content={"status":outcome},headers={"Cache-Control":"no-store"})


class ReschedulePayload(BaseModel):
    model_config=ConfigDict(extra="forbid")
    bayId: UUID
    employeeId: UUID | None = None
    startsAt: datetime
    endsAt: datetime

@router.post("/api/v1/dev/auto/organizations/{org_id}/appointments/{appointment_id}/reschedule")
async def reschedule(org_id: str, appointment_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,ident=UUID(org_id),UUID(appointment_id)
        body=ReschedulePayload.model_validate(await request.json())
        idempotency_header=request.headers.get('Idempotency-Key')
        request_key=UUID(idempotency_header) if idempotency_header is not None else None
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            booking=reschedule_appointment(
                db,request.cookies.get("rp_session",""),org,ident,body.bayId,
                body.startsAt,body.endsAt,body.employeeId,
                clear_employee=('employeeId' in body.model_fields_set and body.employeeId is None),
                request_key=request_key,
            )
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
        except AppointmentKeyConflict:
            return _response(request,409,"IDEMPOTENCY_KEY_REUSED")
        except AppointmentDurationInvalid:
            return _response(request,422,"SERVICES_EXCEED_SLOT")
        except AppointmentHoursClosed:
            return _response(request,409,"LOCATION_CLOSED")
        except AppointmentConflict:
            return _response(request,409,"RESOURCE_BUSY")
    return JSONResponse(content={"status":"RESCHEDULED","bookingId":str(booking)},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/calendar")
def read_calendar(org_id: str, request: Request, locationId: str,
                  startsAt: str | None = None, endsAt: str | None = None,
                  localDay: str | None = None, afterStartsAt: str | None = None,
                  afterAppointmentId: str | None = None):
    from .auto_calendar import (
        calendar_items, local_day_window, CalendarDenied, CalendarInvalid,
    )
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, location = UUID(org_id), UUID(locationId)
        # Ровно один режим окна: legacy absolute или локальные сутки филиала.
        if (localDay is None) == (startsAt is None or endsAt is None):
            raise ValueError("choose a single calendar window")
        if localDay is not None and (startsAt is not None or endsAt is not None):
            raise ValueError("mixed calendar window")
        start = datetime.fromisoformat(startsAt) if startsAt is not None else None
        end = datetime.fromisoformat(endsAt) if endsAt is not None else None
        after_start = datetime.fromisoformat(afterStartsAt) if afterStartsAt is not None else None
        after_appointment = UUID(afterAppointmentId) if afterAppointmentId is not None else None
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            timezone_name = None
            if localDay is not None:
                start, end, timezone_name = local_day_window(
                    db, request.cookies.get("rp_session", ""), org, location, localDay,
                )
            items = calendar_items(
                db, request.cookies.get("rp_session", ""),
                org, location, start, end, after_start, after_appointment,
            )
        except CalendarInvalid:
            return _response(request, 422, "INVALID_INPUT")
        except CalendarDenied:
            return _response(request, 403, "FORBIDDEN")
    page = items[:100]
    more = len(items) > 100
    cursor = ({"nextStartsAt": page[-1]["startsAt"],
               "nextAppointmentId": page[-1]["appointmentId"]} if more else {})
    return JSONResponse(content={"items": page, "hasMore": more, **cursor,
                                 **({"timeZone": timezone_name} if timezone_name else {})},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/slot-candidates")
def read_slot_candidates(org_id: str, request: Request, locationId: str,
                         bayId: str, day: str | None = None,
                         localDay: str | None = None,
                         employeeId: str | None = None,
                         appointmentId: str | None = None):
    from .auto_slot_candidates import candidate_slots, SlotsDenied, parse_slot_services
    from .auto_calendar import local_day_window, CalendarInvalid, CalendarDenied
    from datetime import timezone
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, location, bay = UUID(org_id), UUID(locationId), UUID(bayId)
        employee = UUID(employeeId) if employeeId is not None else None
        appointment = UUID(appointmentId) if appointmentId is not None else None
        selected_services = parse_slot_services(request.query_params.getlist("serviceId"))
        if (day is None) == (localDay is None):
            raise ValueError("exactly one of day or localDay required")
        if day is not None:
            if len(day) != 10 or datetime.strptime(day,"%Y-%m-%d").strftime("%Y-%m-%d") != day:
                raise ValueError("invalid UTC day")
            start = datetime.strptime(day,"%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            zone = None
            end = None
            if localDay is not None:
                start, end, zone = local_day_window(
                    db, request.cookies.get("rp_session", ""), org, location, localDay,
                )
            slots = candidate_slots(
                db, request.cookies.get("rp_session", ""),
                org, location, bay, start, employee, appointment, day_end=end,
                service_ids=selected_services,
            )
            hours_configured = db.execute(
                """SELECT 1 FROM auto_location_opening_policy
                   WHERE location_id=%s AND organization_id=%s""",
                (location,org),
            ).fetchone() is not None
        except CalendarInvalid:
            return _response(request, 422, "INVALID_INPUT")
        except (CalendarDenied, SlotsDenied):
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items": slots, "guaranteed": False,
                                 "openingHoursConfigured":hours_configured,
                                 **({"timeZone": zone} if zone else {})},
                        headers={"Cache-Control": "no-store"})
