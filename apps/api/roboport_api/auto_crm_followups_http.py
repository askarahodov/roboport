"""Closed synthetic CRM follow-up tasks; CSRF and tenant/role enforced."""
from uuid import UUID

import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_crm_leads import LeadDenied,LeadConflict
from .auto_crm_followups import (
    FollowupInvalid,list_followups,create_followup,finish_followup,
)

router=APIRouter(include_in_schema=False)
BASE="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/followups"


class FollowupCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    kind:str
    dueDay:str


class FollowupFinish(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    action:str
    reasonCode:str|None=None


def _context(req,org_id,loc_id,lead_id,task_id=None):
    dsn=_request_dsn(req)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc,lead=UUID(org_id),UUID(loc_id),UUID(lead_id)
        return (dsn,org,loc,lead) if task_id is None else (
            dsn,org,loc,lead,UUID(task_id))
    except (ValueError,TypeError):
        return _response(req,422,"INVALID_INPUT")


def _err(req,e):
    if isinstance(e,LeadConflict):
        return _response(req,409,str(e))
    if isinstance(e,FollowupInvalid) or str(e)=="INVALID_INPUT":
        return _response(req,422,"INVALID_INPUT")
    return _response(req,403,"FORBIDDEN")


@router.get(BASE)
def followup_list(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc,lead=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=list_followups(db,request.cookies.get("rp_session",""),org,loc,lead)
        except (LeadDenied,LeadConflict,FollowupInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post(BASE)
async def followup_create(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    try:
        data=FollowupCreate.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    dsn,org,loc,lead=scoped
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_followup(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                request_key=data.requestKey,kind=data.kind,due_day=data.dueDay,
            )
        except (LeadDenied,LeadConflict,FollowupInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content={"syntheticOnly":True,**result},
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post(BASE+"/{task_id}/finish")
async def followup_finish(org_id:str,loc_id:str,lead_id:str,task_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id,task_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    try:
        data=FollowupFinish.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    dsn,org,loc,lead,task=scoped
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=finish_followup(
                db,request.cookies.get("rp_session",""),org,loc,lead,task,
                request_key=data.requestKey,action=data.action,
                reason_code=data.reasonCode,
            )
        except (LeadDenied,LeadConflict,FollowupInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content={"syntheticOnly":True,**result},
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
