"""Closed synthetic complex catalog and atomic WorkOrder material reservations."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_work_complexes import (
    list_complexes,create_complex,selected_complexes,select_complex,
    ComplexDenied,ComplexConflict,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"


class Material(BaseModel):
    model_config=ConfigDict(extra="forbid")
    skuId:UUID
    itemKind:str=Field(pattern="^(PART|CONSUMABLE)$")
    quantity:int=Field(ge=1,le=1_000_000,strict=True)
    unitPriceMinor:int=Field(ge=0,le=1_000_000_000,strict=True)


class Complex(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    code:str=Field(pattern="^[A-Z0-9_.-]{3,64}$")
    title:str=Field(min_length=1,max_length=120)
    serviceIds:list[UUID]=Field(min_length=1,max_length=50)
    materials:list[Material]=Field(max_length=50)


class Select(BaseModel):
    model_config=ConfigDict(extra="forbid")
    complexId:UUID
    requestKey:UUID


def _scope(request,org_id,loc_id,order_id=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        identifiers=[UUID(org_id),UUID(loc_id)]
        if order_id is not None:
            identifiers.append(UUID(order_id))
        return dsn,identifiers
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,ComplexConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get(prefix+"/work-complexes")
def catalog(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc)=scoped
    with psycopg.connect(dsn) as db:
        try:
            data=list_complexes(db,request.cookies.get("rp_session",""),org,loc)
        except (ComplexDenied,ComplexConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/work-complexes")
async def create(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc)=scoped
    try:
        body=Complex.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            data=create_complex(db,request.cookies.get("rp_session",""),org,loc,
                request_key=body.requestKey,code=body.code,title=body.title,
                services=body.serviceIds,
                materials=[{"skuId":m.skuId,"itemKind":m.itemKind,
                            "quantity":m.quantity,"unitPriceMinor":m.unitPriceMinor}
                           for m in body.materials])
        except (ComplexDenied,ComplexConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})


@router.get(prefix+"/orders/{order_id}/complexes")
def selected(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,order)=scoped
    with psycopg.connect(dsn) as db:
        try:
            data=selected_complexes(db,request.cookies.get("rp_session",""),org,loc,order)
        except (ComplexDenied,ComplexConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/orders/{order_id}/complexes")
async def select(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,order)=scoped
    try:
        body=Select.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            data=select_complex(db,request.cookies.get("rp_session",""),org,loc,order,
                request_key=body.requestKey,complex_id=body.complexId)
        except (ComplexDenied,ComplexConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if data["replayed"] else 201,
                        content=data,headers={"Cache-Control":"no-store"})
