"""Manager-only synthetic tariff proposals and independent approvals."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_work_tariffs import (
    read_tariffs,propose_tariff,approve_tariff,TariffDenied,TariffConflict,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/tariff-policies"


class Proposal(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    expectedVersion:int=Field(ge=0,le=29,strict=True)
    qualificationCode:str=Field(pattern="^(GENERAL|DIAGNOSTIC|MASTER)$")
    rateMinorPerHour:int=Field(ge=0,le=10**10,strict=True)
    maxDiscountBps:int=Field(ge=0,le=2500,strict=True)


class Approval(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _scope(request,org_id,loc_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,error):
    if isinstance(error,TariffConflict):
        return _response(request,409,str(error))
    if str(error)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get(prefix)
def list_policies(org_id:str,loc_id:str,request:Request):
    scope=_scope(request,org_id,loc_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc=scope
    with psycopg.connect(dsn) as db:
        try:
            data=read_tariffs(db,request.cookies.get("rp_session",""),org,loc)
        except (TariffDenied,TariffConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix)
async def propose(org_id:str,loc_id:str,request:Request):
    scope=_scope(request,org_id,loc_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc=scope
    try:
        payload=Proposal.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            data=propose_tariff(db,request.cookies.get("rp_session",""),org,loc,
                request_key=payload.requestKey,expected_version=payload.expectedVersion,
                qualification_code=payload.qualificationCode,
                rate_minor_per_hour=payload.rateMinorPerHour,
                max_discount_bps=payload.maxDiscountBps)
        except (TariffDenied,TariffConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/{policy_id}/approve")
async def approve(org_id:str,loc_id:str,policy_id:str,request:Request):
    scope=_scope(request,org_id,loc_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc=scope
    try:
        ident=UUID(policy_id)
        payload=Approval.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            data=approve_tariff(db,request.cookies.get("rp_session",""),org,loc,
                               ident,payload.requestKey)
        except (TariffDenied,TariffConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})
