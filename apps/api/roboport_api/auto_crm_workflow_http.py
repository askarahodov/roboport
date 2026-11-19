"""Private synthetic CRM Kanban, workflow config and assignment endpoints."""
import psycopg
from uuid import UUID
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _response
from .auto_crm_leads_http import _context,_error
from .auto_crm_leads import LeadDenied,LeadConflict
from .auto_crm_workflow import get_workflow_config,set_workflow_config,assign_lead,list_kanban

router=APIRouter(include_in_schema=False)


class ConfigBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    expectedRevision:int=Field(strict=True,ge=0,lt=2147483647)
    requestKey:UUID
    config:dict


class AssignmentBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    assigneeId:UUID
    requestKey:UUID


def _workflow_error(request,exc):
    return _error(request,exc)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/workflow")
def crm_workflow(org_id:str,loc_id:str,request:Request):
    context=_context(request,org_id,loc_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc)=context
    with psycopg.connect(dsn) as db:
        try:
            result=get_workflow_config(db,request.cookies.get("rp_session",""),org,loc)
        except (LeadDenied,LeadConflict) as exc:
            return _workflow_error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/workflow")
async def update_crm_workflow(org_id:str,loc_id:str,request:Request):
    context=_context(request,org_id,loc_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc)=context
    try:
        body=ConfigBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=set_workflow_config(
                db,request.cookies.get("rp_session",""),org,loc,
                body.expectedRevision,body.requestKey,body.config,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _workflow_error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/kanban")
def crm_kanban(org_id:str,loc_id:str,request:Request):
    context=_context(request,org_id,loc_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc)=context
    with psycopg.connect(dsn) as db:
        try:
            result=list_kanban(db,request.cookies.get("rp_session",""),org,loc)
        except (LeadDenied,LeadConflict) as exc:
            return _workflow_error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/assignment")
async def crm_assign(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_context(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    try:
        body=AssignmentBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=assign_lead(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                body.assigneeId,body.requestKey,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _workflow_error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
