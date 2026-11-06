"""Closed HTTPS-loopback diagnostic findings: staff only, no free text or PII."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_work_order_diagnostics import (
    DiagnosticDenied,DiagnosticConflict,read_diagnostics,record_diagnostic,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/diagnostics"


class Finding(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    serviceId:UUID
    findingCode:str
    severityCode:str


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,DiagnosticConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get(prefix)
def read(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    with psycopg.connect(dsn) as conn:
        try:
            state=read_diagnostics(conn,request.cookies.get("rp_session",""),
                                   org,loc,order)
        except (DiagnosticDenied,DiagnosticConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=state,headers={"Cache-Control":"no-store"})


@router.post(prefix)
async def write(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        payload=Finding.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            state=record_diagnostic(conn,request.cookies.get("rp_session",""),
                                    org,loc,order,request_key=payload.requestKey,
                                    service_id=payload.serviceId,
                                    finding_code=payload.findingCode,
                                    severity_code=payload.severityCode)
        except (DiagnosticDenied,DiagnosticConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=state,status_code=200 if state["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
