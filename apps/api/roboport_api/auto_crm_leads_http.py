"""Synthetic-only CRM lead HTTP: tenant-scoped, CSRF-protected mutations."""
from uuid import UUID
from datetime import datetime

import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError

from .auth_http import _mutation_ok
from .auto_crm_deal import read_deal
from .auto_crm_interactions import list_interactions, record_interaction
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_crm_leads import (
    LeadDenied,LeadConflict,create_lead,transition_lead,lead_detail,list_leads,
)

router=APIRouter(include_in_schema=False)


class CreateBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    customerLinkId:UUID
    sourceCode:str
    requestKey:UUID


class StageBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    stageCode:str
    reasonCode:str|None=None
    appointmentId:UUID|None=None
    requestKey:UUID


def _context(request,org_id,loc_id,lead_id=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        identifiers=[UUID(org_id),UUID(loc_id)]
        if lead_id is not None:
            identifiers.append(UUID(lead_id))
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return dsn,identifiers


def _error(request,exc):
    if isinstance(exc,LeadConflict):
        return _response(request,409,"CONFLICT")
    return _response(request,422 if str(exc)=="INVALID_INPUT" else 403,
                     "INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN")


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads")
def crm_leads(org_id:str,loc_id:str,request:Request,cursor:str|None=None):
    scoped=_context(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc)=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=list_leads(db,request.cookies.get("rp_session",""),org,loc,cursor)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}")
def crm_detail(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,lead)=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=lead_detail(db,request.cookies.get("rp_session",""),org,loc,lead)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads")
async def crm_create(org_id:str,loc_id:str,request:Request):
    scoped=_context(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc)=scoped
    try:
        data=CreateBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_lead(db,request.cookies.get("rp_session",""),
                               org,loc,data.customerLinkId,data.sourceCode,data.requestKey)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/stages")
async def crm_stage(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,lead)=scoped
    try:
        data=StageBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=transition_lead(
                db,request.cookies.get("rp_session",""),org,loc,
                lead,data.requestKey,data.stageCode,data.reasonCode,data.appointmentId,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/deal")
def crm_deal(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,lead)=scoped
    try:
        with psycopg.connect(dsn) as db:
            result=read_deal(db,request.cookies.get("rp_session",""),org,loc,lead)
    except (LeadDenied,LeadConflict) as exc:
        return _error(request,exc)
    except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
        return _response(request,409,"CONFLICT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


class InteractionBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:str
    outcomeCode:str
    requestKey:UUID
    callbackDueAt:datetime|None=None
    expectedActorId:UUID|None=None


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/interactions")
def crm_interactions(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,lead)=scoped
    with psycopg.connect(dsn) as db:
        try:
            cursors=request.query_params.getlist("cursor")
            if len(cursors)>1 or (cursors and not cursors[0]):
                raise LeadDenied("INVALID_INPUT")
            result=list_interactions(db,request.cookies.get("rp_session",""),org,loc,lead,
                                     cursors[0] if cursors else None)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/interactions")
async def crm_interaction(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_context(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,(org,loc,lead)=scoped
    try:
        body=InteractionBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=record_interaction(db,request.cookies.get("rp_session",""),org,loc,lead,
                                      body.requestKey,body.channelCode,body.outcomeCode,
                                      body.callbackDueAt,body.expectedActorId)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
        except (psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure):
            return _response(request,409,"CONFLICT")
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
