"""Closed dev-only automotive estimates: publish priced snapshot, client decision."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field
from .auto_vehicle_claim_http import _request_dsn, _response
from .auth_http import _mutation_ok
from .auto_work_order_estimate import (
    AutoEstimateDenied, AutoEstimateConflict,
    read_auto_estimates, publish_auto_estimate, decide_auto_estimate,
    request_estimate_clarification, resolve_estimate_clarification,
)

router=APIRouter(include_in_schema=False)


class Price(BaseModel):
    model_config=ConfigDict(extra="forbid")
    serviceId: UUID
    amountMinor: int=Field(ge=0,le=10**12)


class LaborRate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    serviceId: UUID
    qualificationCode: str=Field(pattern="^(GENERAL|DIAGNOSTIC|MASTER)$")
    rateMinorPerHour: int=Field(ge=0,le=10**10,strict=True)


class Publish(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey: UUID
    expectedVersion: int=Field(ge=0,le=29)
    prices: list[Price] | None=Field(default=None,min_length=1,max_length=50)
    laborRates: list[LaborRate] | None=Field(default=None,min_length=1,max_length=50)


class Decision(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revisionId: UUID
    requestKey: UUID
    decision: str


class Clarification(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revisionId: UUID
    requestKey: UUID
    reasonCode: str=Field(pattern="^(PRICE|WORK_SCOPE|TIMING|OTHER)$")


class ClarificationResolution(BaseModel):
    model_config=ConfigDict(extra="forbid")
    revisionId: UUID
    requestKey: UUID
    resolutionCode: str=Field(pattern="^(EXPLAINED|NEW_VERSION_REQUIRED)$")


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,AutoEstimateConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/estimates")
def read(org_id:str,loc_id:str,order_id:str,request:Request):
    values=_scope(request,org_id,loc_id,order_id)
    if isinstance(values,JSONResponse):
        return values
    dsn,org,loc,order=values
    with psycopg.connect(dsn) as conn:
        try:
            data=read_auto_estimates(conn,request.cookies.get("rp_session",""),org,loc,order)
        except (AutoEstimateDenied,AutoEstimateConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/estimates")
async def publish(org_id:str,loc_id:str,order_id:str,request:Request):
    values=_scope(request,org_id,loc_id,order_id)
    if isinstance(values,JSONResponse):
        return values
    dsn,org,loc,order=values
    try:
        body=Publish.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    if (body.prices is None)==(body.laborRates is None):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=publish_auto_estimate(conn,request.cookies.get("rp_session",""),
                org,loc,order,request_key=body.requestKey,
                expected_version=body.expectedVersion,
                prices=[{"serviceId":p.serviceId,"amountMinor":p.amountMinor}
                        for p in body.prices] if body.prices is not None else None,
                labor_rates=[{"serviceId":p.serviceId,
                              "qualificationCode":p.qualificationCode,
                              "rateMinorPerHour":p.rateMinorPerHour}
                             for p in body.laborRates] if body.laborRates is not None else None)
        except (AutoEstimateDenied,AutoEstimateConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/estimates/decision")
async def decision(org_id:str,loc_id:str,order_id:str,request:Request):
    values=_scope(request,org_id,loc_id,order_id)
    if isinstance(values,JSONResponse):
        return values
    dsn,org,loc,order=values
    try:
        body=Decision.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=decide_auto_estimate(conn,request.cookies.get("rp_session",""),
                org,loc,order,revision_id=body.revisionId,
                request_key=body.requestKey,decision=body.decision)
        except (AutoEstimateDenied,AutoEstimateConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/estimates/clarification")
async def ask_clarification(org_id:str,loc_id:str,order_id:str,request:Request):
    values=_scope(request,org_id,loc_id,order_id)
    if isinstance(values,JSONResponse):
        return values
    dsn,org,loc,order=values
    try:
        body=Clarification.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=request_estimate_clarification(
                conn,request.cookies.get("rp_session",""),org,loc,order,
                revision_id=body.revisionId,request_key=body.requestKey,
                reason_code=body.reasonCode)
        except (AutoEstimateDenied,AutoEstimateConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/estimates/clarification/resolve")
async def answer_clarification(org_id:str,loc_id:str,order_id:str,request:Request):
    values=_scope(request,org_id,loc_id,order_id)
    if isinstance(values,JSONResponse):
        return values
    dsn,org,loc,order=values
    try:
        body=ClarificationResolution.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            data=resolve_estimate_clarification(
                conn,request.cookies.get("rp_session",""),org,loc,order,
                revision_id=body.revisionId,request_key=body.requestKey,
                resolution_code=body.resolutionCode)
        except (AutoEstimateDenied,AutoEstimateConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})
