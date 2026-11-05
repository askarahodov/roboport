"""Closed synthetic CRM follow-up HTTP surface; no outgoing communication."""
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_crm_followups import create_followup, list_followups, resolve_followup, create_post_service_review
from .auto_crm_leads import LeadDenied, LeadConflict

router=APIRouter(include_in_schema=False)


class TaskBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    taskCode:str
    dueAt:datetime


class ServiceReviewBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    workOrderId:UUID
    dueAt:datetime


class ResolutionBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    action:str


def _scope(request,org_id,loc_id,lead_id,task_id=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        parts=[UUID(x) for x in (org_id,loc_id,lead_id)]
        if task_id is not None:
            parts.append(UUID(task_id))
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return dsn,parts


def _error(request,exc):
    if isinstance(exc,LeadConflict):
        return _response(request,409,"CONFLICT")
    return _response(request,422 if str(exc)=="INVALID_INPUT" else 403,
                     "INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/tasks")
def get_followups(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_scope(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    with psycopg.connect(dsn) as db:
        try:
            result=list_followups(db,request.cookies.get("rp_session",""),org,loc,lead)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/tasks")
async def post_followup(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_scope(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    try:
        data=TaskBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_followup(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                data.requestKey,data.taskCode,data.dueAt,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/tasks/{task_id}/events")
async def post_followup_resolution(org_id:str,loc_id:str,lead_id:str,
                                   task_id:str,request:Request):
    context=_scope(request,org_id,loc_id,lead_id,task_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead,task)=context
    try:
        data=ResolutionBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=resolve_followup(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                task,data.requestKey,data.action,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/service-review")
async def post_service_review(org_id:str,loc_id:str,lead_id:str,request:Request):
    """Manual staff action only; no external customer message or scheduling daemon."""
    context=_scope(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    try:
        data=ServiceReviewBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_post_service_review(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                data.requestKey,data.workOrderId,data.dueAt,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
