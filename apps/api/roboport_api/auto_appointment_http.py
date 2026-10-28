"""Synthetic loopback-only appointment create and cancellation endpoints."""
from datetime import datetime
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_appointment_service import (
    create_appointment, cancel_appointment, reschedule_appointment, AppointmentDenied, AppointmentConflict,
)
router=APIRouter(include_in_schema=False)

class CreateAppointment(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    bayId: UUID
    customerLinkId: UUID
    startsAt: datetime
    endsAt: datetime

@router.post("/api/v1/dev/auto/organizations/{org_id}/appointments")
async def create(org_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        body=CreateAppointment.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            appointment,booking,work_order=create_appointment(
                db,request.cookies.get("rp_session",""),org,body.locationId,
                body.bayId,body.customerLinkId,body.startsAt,body.endsAt,
            )
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
        except AppointmentConflict:
            return _response(request,409,"BAY_BUSY")
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
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            outcome=cancel_appointment(db,request.cookies.get("rp_session",""),org,ident)
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content={"status":outcome},headers={"Cache-Control":"no-store"})


class ReschedulePayload(BaseModel):
    model_config=ConfigDict(extra="forbid")
    bayId: UUID
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
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            booking=reschedule_appointment(
                db,request.cookies.get("rp_session",""),org,ident,body.bayId,
                body.startsAt,body.endsAt,
            )
        except AppointmentDenied:
            return _response(request,403,"FORBIDDEN")
        except AppointmentConflict:
            return _response(request,409,"BAY_BUSY")
    return JSONResponse(content={"status":"RESCHEDULED","bookingId":str(booking)},
                        headers={"Cache-Control":"no-store"})
