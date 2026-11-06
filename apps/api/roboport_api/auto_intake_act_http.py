"""Closed HTTPS automotive intake-act and private image routes, no external URLs."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_intake_act import (
    IntakeActDenied,IntakeActInvalid,IntakeActConflict,read_intake_acts,
    write_intake_act,read_act_photo,
)

router=APIRouter(include_in_schema=False)
PATH="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/intake-acts"


class Damage(BaseModel):
    model_config=ConfigDict(extra="forbid")
    areaCode:str=Field(pattern="^(FRONT|REAR|LEFT|RIGHT|ROOF|CABIN)$")
    damageCode:str=Field(pattern="^(SCRATCH|DENT|CRACK|MISSING|CORROSION)$")
    severity:str=Field(pattern="^(MINOR|MODERATE|SEVERE)$")


class Inventory(BaseModel):
    model_config=ConfigDict(extra="forbid")
    itemCode:str=Field(pattern="^(KEYS|SPARE_WHEEL|TOOLS|DOCS|CHARGER)$")
    condition:str=Field(pattern="^(PRESENT|MISSING|DAMAGED|NOT_CHECKED)$")


class Photo(BaseModel):
    model_config=ConfigDict(extra="forbid")
    areaCode:str=Field(pattern="^(FRONT|REAR|LEFT|RIGHT|ROOF|CABIN)$")
    imageBase64:str=Field(min_length=1,max_length=262144)


class Act(BaseModel):
    model_config=ConfigDict(extra="forbid")
    vehicleKind:str=Field(pattern="^(PASSENGER|SUV|VAN|TRUCK|MOTORCYCLE)$")
    damages:list[Damage]=Field(max_length=12)
    inventory:list[Inventory]=Field(min_length=5,max_length=5)
    photos:list[Photo]=Field(max_length=3)


class Save(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    expectedVersion:int=Field(ge=0,le=19,strict=True)
    act:Act


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _err(request,error):
    if isinstance(error,IntakeActConflict):
        return _response(request,409,str(error))
    if isinstance(error,IntakeActInvalid):
        return _response(request,422,str(error))
    return _response(request,403,"FORBIDDEN")


@router.get(PATH)
def list_acts(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    with psycopg.connect(dsn) as conn:
        try:
            state=read_intake_acts(conn,request.cookies.get("rp_session",""),
                                   org,loc,order)
        except (IntakeActDenied,IntakeActInvalid,IntakeActConflict) as error:
            return _err(request,error)
    return JSONResponse(content=state,headers={"Cache-Control":"no-store"})


@router.post(PATH)
async def save_act(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        body=Save.model_validate(await request.json())
    except (TypeError,ValueError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):
            return _response(request,403,"FORBIDDEN")
        try:
            state=write_intake_act(
                conn,request.cookies.get("rp_session",""),org,loc,order,
                request_key=body.requestKey,expected_version=body.expectedVersion,
                act=body.act.model_dump())
        except (IntakeActDenied,IntakeActInvalid,IntakeActConflict) as error:
            return _err(request,error)
    return JSONResponse(status_code=200 if state["replayed"] else 201,
                        content=state,headers={"Cache-Control":"no-store"})


@router.get(PATH+"/{act_id}/photos/{index}")
def photo(org_id:str,loc_id:str,order_id:str,act_id:str,index:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        ident=UUID(act_id)
        idx=int(index)
        if idx<1 or idx>3 or str(idx)!=index:
            raise ValueError("invalid photo")
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        try:
            payload=read_act_photo(
                conn,request.cookies.get("rp_session",""),
                org,loc,order,ident,idx)
        except (IntakeActDenied,IntakeActInvalid,IntakeActConflict) as error:
            return _err(request,error)
    return Response(content=payload,media_type="image/png",headers={
        "Cache-Control":"no-store, private",
        "X-Content-Type-Options":"nosniff",
        "Content-Security-Policy":"sandbox",
        "Cross-Origin-Resource-Policy":"same-origin",
    })
