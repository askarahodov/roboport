"""Loopback-only automobile intake inspection and independent QC endpoints."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from .auto_vehicle_claim_http import _request_dsn, _response
from .auth_http import _mutation_ok
from .auto_workshop_qc import (
    InspectionConflict, InspectionDenied, read_work_order_inspection,
    submit_work_order_inspection, record_work_order_qc,
)

router = APIRouter(include_in_schema=False)


class InspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    odometerKm: int
    fuelPercent: int
    tyresOk: bool
    lightsOk: bool
    fluidLeak: bool
    exteriorCode: str


class QcInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requestKey: UUID
    result: str
    findingCode: str


def _identifiers(request, org_id, loc_id, order_id):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        return dsn, UUID(org_id), UUID(loc_id), UUID(order_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")


def _denied_response(request, error):
    if isinstance(error, InspectionConflict):
        return _response(request, 409, str(error))
    if str(error) == "INVALID_INPUT":
        return _response(request, 422, "INVALID_INPUT")
    return _response(request, 403, "FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/inspection-qc")
def read_inspection_qc(org_id: str, loc_id: str, order_id: str, request: Request):
    scoped = _identifiers(request, org_id, loc_id, order_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, org, loc, order = scoped
    with psycopg.connect(dsn) as conn:
        try:
            outcome = read_work_order_inspection(
                conn, request.cookies.get("rp_session",""), org, loc, order)
        except (InspectionDenied, InspectionConflict) as error:
            return _denied_response(request,error)
    return JSONResponse(content=outcome,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/inspection")
async def inspect(org_id: str, loc_id: str, order_id: str, request: Request):
    scoped = _identifiers(request, org_id, loc_id, order_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, org, loc, order = scoped
    try:
        data=InspectionInput.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            outcome=submit_work_order_inspection(
                conn,request.cookies.get("rp_session",""),org,loc,order,
                odometer_km=data.odometerKm,fuel_percent=data.fuelPercent,
                tyres_ok=data.tyresOk,lights_ok=data.lightsOk,
                fluid_leak=data.fluidLeak,exterior_code=data.exteriorCode)
        except (InspectionDenied, InspectionConflict) as error:
            return _denied_response(request,error)
    return JSONResponse(status_code=200 if outcome["replayed"] else 201,
                        content=outcome,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/qc")
async def qc(org_id: str, loc_id: str, order_id: str, request: Request):
    scoped = _identifiers(request, org_id, loc_id, order_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, org, loc, order = scoped
    try:
        data=QcInput.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            outcome=record_work_order_qc(
                conn,request.cookies.get("rp_session",""),org,loc,order,
                request_key=data.requestKey,result=data.result,
                finding_code=data.findingCode)
        except (InspectionDenied, InspectionConflict) as error:
            return _denied_response(request,error)
    return JSONResponse(status_code=200 if outcome["replayed"] else 201,
                        content=outcome,headers={"Cache-Control":"no-store"})
