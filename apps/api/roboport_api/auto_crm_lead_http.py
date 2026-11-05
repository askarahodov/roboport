"""Synthetic-only closed CRM API; no PII or external communication."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError

from .auto_vehicle_claim_http import _request_dsn,_response
from .auth_http import _mutation_ok
from .auto_crm_lead_service import (
    CrmDenied,CrmInvalid,CrmConflict,create_lead,list_leads,
    transition_lead,link_appointment,
)

router=APIRouter(include_in_schema=False)


class LeadCreate(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    intakeKey:UUID
    ownerId:UUID
    sourceCode:str


class LeadStage(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    stage:str
    reasonCode:str|None=None


class LeadBooking(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    appointmentId:UUID


def _scope(req,org_id,loc_id,lead_id=None):
    dsn=_request_dsn(req)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        return (dsn,org,loc) if lead_id is None else (dsn,org,loc,UUID(lead_id))
    except (ValueError,TypeError):
        return _response(req,422,"INVALID_INPUT")


def _err(req,e):
    if isinstance(e,CrmInvalid):
        return _response(req,422,"INVALID_INPUT")
    if isinstance(e,CrmConflict):
        return _response(req,409,str(e))
    return _response(req,403,"FORBIDDEN")


BASE="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm-leads"


@router.get(BASE)
def crm_leads_get(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=list_leads(db,request.cookies.get("rp_session",""),org,loc)
        except (CrmDenied,CrmInvalid,CrmConflict) as e:
            return _err(request,e)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post(BASE)
async def crm_leads_create(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    try:
        body=LeadCreate.model_validate(await request.json())
    except (ValueError,TypeError,UnicodeDecodeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    dsn,org,loc=scoped
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_lead(
                db,request.cookies.get("rp_session",""),org,loc,
                owner_id=body.ownerId,source_code=body.sourceCode,
                intake_key=body.intakeKey,request_key=body.requestKey,
            )
        except (CrmDenied,CrmInvalid,CrmConflict) as e:
            return _err(request,e)
    return JSONResponse(content={"syntheticOnly":True,**result},
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post(BASE+"/{lead_id}/booking")
async def crm_leads_booking(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    try:
        body=LeadBooking.model_validate(await request.json())
    except (ValueError,TypeError,UnicodeDecodeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    dsn,org,loc,ident=scoped
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=link_appointment(
                db,request.cookies.get("rp_session",""),org,loc,ident,
                appointment_id=body.appointmentId,request_key=body.requestKey,
            )
        except (CrmDenied,CrmInvalid,CrmConflict) as e:
            return _err(request,e)
    return JSONResponse(content={"syntheticOnly":True,**result},
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.post(BASE+"/{lead_id}/stage")
async def crm_leads_stage(org_id:str,loc_id:str,lead_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,lead_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    try:
        body=LeadStage.model_validate(await request.json())
    except (ValueError,TypeError,UnicodeDecodeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    dsn,org,loc,ident=scoped
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=transition_lead(
                db,request.cookies.get("rp_session",""),org,loc,ident,
                stage=body.stage,reason_code=body.reasonCode,request_key=body.requestKey,
            )
        except (CrmDenied,CrmInvalid,CrmConflict) as e:
            return _err(request,e)
    return JSONResponse(content={"syntheticOnly":True,**result},
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
