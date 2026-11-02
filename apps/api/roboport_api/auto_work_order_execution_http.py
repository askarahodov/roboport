"""HTTPS loopback synthetic execution, labor and stock handoff lifecycle."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_work_order_execution import (
  ExecutionDenied,ExecutionConflict,read_execution,start_execution,labor_execution,
  issue_material,receive_material,finish_execution,
)

router=APIRouter(include_in_schema=False)


class Key(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


class Labor(Key):
    serviceId:UUID
    actualMinutes:int=Field(ge=1,le=1440)


class Issue(Key):
    lotId:UUID
    quantity:int=Field(ge=1,le=1_000_000_000)


class Receipt(Key):
    issueId:UUID


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")


def _denial(request,error):
    if isinstance(error,ExecutionConflict):
        return _response(request,409,str(error))
    if str(error)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/execution")
def read(org_id:str,loc_id:str,order_id:str,request:Request):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    with psycopg.connect(dsn) as db:
        try:
            data=read_execution(db,request.cookies.get("rp_session",""),org,loc,order)
        except (ExecutionDenied,ExecutionConflict) as exc:
            return _denial(request,exc)
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


async def _write(request,org_id,loc_id,order_id,action):
    scope=_scope(request,org_id,loc_id,order_id)
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc,order=scope
    try:
        raw=await request.json()
        if action in ("start","finish"):
            payload=Key.model_validate(raw)
        elif action=="labor":
            payload=Labor.model_validate(raw)
        elif action=="issue":
            payload=Issue.model_validate(raw)
        else:
            payload=Receipt.model_validate(raw)
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        token=request.cookies.get("rp_session","")
        try:
            if action=="start":
                data=start_execution(db,token,org,loc,order,payload.requestKey)
            elif action=="finish":
                data=finish_execution(db,token,org,loc,order,payload.requestKey)
            elif action=="labor":
                data=labor_execution(db,token,org,loc,order,
                    payload.serviceId,payload.actualMinutes,payload.requestKey)
            elif action=="issue":
                data=issue_material(db,token,org,loc,order,
                    payload.lotId,payload.quantity,payload.requestKey)
            else:
                data=receive_material(db,token,org,loc,order,
                    payload.issueId,payload.requestKey)
        except (ExecutionDenied,ExecutionConflict) as exc:
            return _denial(request,exc)
    return JSONResponse(status_code=200 if data.get("replayed") else 201,
                        content=data,headers={"Cache-Control":"no-store"})


prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/execution"


@router.post(prefix+"/start")
async def start(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"start")


@router.post(prefix+"/labor")
async def labor(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"labor")


@router.post(prefix+"/materials/issue")
async def stock_issue(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"issue")


@router.post(prefix+"/materials/receive")
async def handoff(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"receive")


@router.post(prefix+"/finish")
async def finish(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"finish")
