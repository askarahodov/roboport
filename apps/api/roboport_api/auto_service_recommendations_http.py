"""HTTPS loopback: план ТО сотрудника и исходного клиента."""
from datetime import date, datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .auth_http import _mutation_ok
from .auto_maintenance_queue import read_maintenance_queue, QueueDenied, QueueInvalid
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_service_recommendations import (
    RecommendationDenied, RecommendationConflict, read_recommendations,
    create_recommendation, withdraw_recommendation, repeat_visit_options, book_repeat_visit,
    search_recommendation_services,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/recommendations"


class CreateBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    serviceId:UUID
    reasonCode:str
    dueDate:date
    dueKm:int|None=Field(default=None,strict=True,ge=0,le=2000000)


class WithdrawBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,RecommendationConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


def _read(request,org_id,loc_id,order_id,customer):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    with psycopg.connect(dsn) as db:
        try:
            result=read_recommendations(db,request.cookies.get("rp_session",""),
                                        org,loc,order,customer=customer)
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get(prefix)
def staff_read(org_id:str,loc_id:str,order_id:str,request:Request):
    return _read(request,org_id,loc_id,order_id,False)


@router.get(prefix+"/customer")
def customer_read(org_id:str,loc_id:str,order_id:str,request:Request):
    return _read(request,org_id,loc_id,order_id,True)


@router.post(prefix)
async def create(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        body=CreateBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_recommendation(db,request.cookies.get("rp_session",""),
                org,loc,order,key=body.requestKey,service=body.serviceId,
                reason=body.reasonCode,due=body.dueDate,km=body.dueKm)
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post(prefix+"/{recommendation_id}/withdraw")
async def withdraw(org_id:str,loc_id:str,order_id:str,recommendation_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        ident=UUID(recommendation_id)
        body=WithdrawBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=withdraw_recommendation(db,request.cookies.get("rp_session",""),
                org,loc,order,key=body.requestKey,ident=ident)
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


class VisitBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    bayId:UUID
    startsAt:datetime
    endsAt:datetime


@router.get(prefix+"/{recommendation_id}/visit")
def visit_options(org_id:str,loc_id:str,order_id:str,recommendation_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        ident=UUID(recommendation_id)
        bays=request.query_params.getlist("bayId")
        days=request.query_params.getlist("localDay")
        if len(bays)!=len(days) or len(bays)>1:
            raise ValueError("scope")
        bay=UUID(bays[0]) if bays else None
        day=date.fromisoformat(days[0]) if days else None
        if day is not None and day.isoformat()!=days[0]:
            raise ValueError("date")
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=repeat_visit_options(db,request.cookies.get("rp_session",""),
                                        org,loc,order,ident,bay=bay,day=day)
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/{recommendation_id}/visit")
async def visit_create(org_id:str,loc_id:str,order_id:str,recommendation_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        ident=UUID(recommendation_id)
        body=VisitBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=book_repeat_visit(db,request.cookies.get("rp_session",""),org,loc,order,
                ident,key=body.requestKey,bay=body.bayId,starts=body.startsAt,ends=body.endsAt)
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.get(prefix+"/services")
def search_services(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    queries=request.query_params.getlist("q")
    if len(queries)>1:
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=search_recommendation_services(db,request.cookies.get("rp_session",""),
                                                 org,loc,order,queries[0] if queries else "")
        except (RecommendationDenied,RecommendationConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/maintenance-queue")
def maintenance_queue(org_id:str,loc_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        query=request.query_params
        if any(len(query.getlist(k))>1 for k in ("mode","horizon","cursor","reasonCode")):
            raise ValueError("duplicate")
        horizon=query.get("horizon","30")
        if horizon not in ("7","30","90"):
            raise ValueError("horizon")
        mode=query.get("mode","ALL")
        cursor=query.get("cursor")
        if cursor=="":
            raise ValueError("cursor")
        with psycopg.connect(dsn) as db:
            result=read_maintenance_queue(db,request.cookies.get("rp_session",""),org,loc,
                                          mode,int(horizon),cursor,query.get("reasonCode","ALL"))
    except QueueDenied:
        return _response(request,403,"FORBIDDEN")
    except (QueueInvalid,ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
