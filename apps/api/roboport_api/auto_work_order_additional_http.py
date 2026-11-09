"""Scoped synthetic extra-work API with independent customer consent."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_org_authority import organization_role_allows
from .auto_work_order_additional import (
    read_extra,publish_extra,decide_extra,ExtraDenied,ExtraConflict,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/extras"


class WorkRate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    serviceId:UUID
    qualificationCode:str=Field(pattern="^(GENERAL|DIAGNOSTIC|MASTER)$")
    rateMinorPerHour:int=Field(ge=0,le=10**10,strict=True)
    quantity:int=Field(ge=1,le=20,strict=True)
    coefficientBps:int=Field(ge=10000,le=30000,strict=True)
    discountBps:int=Field(ge=0,le=2500,strict=True)


class Publish(BaseModel):
    model_config=ConfigDict(extra="forbid")
    baseRevisionId:UUID
    requestKey:UUID
    expectedVersion:int=Field(ge=0,le=9,strict=True)
    workRates:list[WorkRate]=Field(min_length=1,max_length=20)


class Decision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revisionId:UUID
    requestKey:UUID
    decision:str=Field(pattern="^(APPROVED|DECLINED)$")


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,ExtraConflict):
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
        token=request.cookies.get("rp_session","")
        try:
            manager=organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)
            data=read_extra(conn,token,org,loc,order,manager)
        except (ExtraDenied,ExtraConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix)
async def publish(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        payload=Publish.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=publish_extra(conn,request.cookies.get("rp_session",""),org,loc,order,
                base_revision_id=payload.baseRevisionId,request_key=payload.requestKey,
                expected_version=payload.expectedVersion,
                work_rates=[{
                    "serviceId":p.serviceId,"qualificationCode":p.qualificationCode,
                    "rateMinorPerHour":p.rateMinorPerHour,"quantity":p.quantity,
                    "coefficientBps":p.coefficientBps,"discountBps":p.discountBps,
                } for p in payload.workRates])
        except (ExtraDenied,ExtraConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/decision")
async def decision(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        payload=Decision.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=decide_extra(conn,request.cookies.get("rp_session",""),org,loc,order,
                revision_id=payload.revisionId,request_key=payload.requestKey,
                decision=payload.decision)
        except (ExtraDenied,ExtraConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})
