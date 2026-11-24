"""Private synthetic CRM consent and inert mock outbox HTTP endpoints."""
import psycopg
from uuid import UUID
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_crm_leads_http import _context,_error
from .auto_crm_leads import LeadDenied,LeadConflict
from .auto_crm_consent import (
    read_customer_consent,change_customer_consent,read_staff_consent,
    create_mock_message,list_mock_messages,
)

router=APIRouter(include_in_schema=False)


class ConsentBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:str
    enabled:bool=Field(strict=True)
    expectedRevision:int=Field(strict=True,ge=0,lt=2147483647)
    requestKey:UUID


class MockMessageBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:str
    templateCode:str
    requestKey:UUID


@router.get("/api/v1/dev/account/crm-contact-consent")
def customer_crm_consent(request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    with psycopg.connect(dsn) as db:
        try:
            result=read_customer_consent(db,request.cookies.get("rp_session",""))
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/account/crm-contact-consent")
async def update_customer_crm_consent(request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        body=ConsentBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=change_customer_consent(
                db,request.cookies.get("rp_session",""),body.channelCode,
                body.enabled,body.expectedRevision,body.requestKey,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/contact-consent")
def staff_crm_consent(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_context(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    with psycopg.connect(dsn) as db:
        try:
            result=read_staff_consent(db,request.cookies.get("rp_session",""),
                                      org,loc,lead)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/mock-messages")
def staff_crm_mock_messages(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_context(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    with psycopg.connect(dsn) as db:
        try:
            cursors=request.query_params.getlist("cursor")
            if len(cursors)>1 or (cursors and not cursors[0]):
                raise LeadDenied("INVALID_INPUT")
            result=list_mock_messages(db,request.cookies.get("rp_session",""),
                                      org,loc,lead,cursors[0] if cursors else None)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/mock-messages")
async def create_staff_crm_mock_message(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_context(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    try:
        body=MockMessageBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=create_mock_message(
                db,request.cookies.get("rp_session",""),org,loc,lead,
                body.requestKey,body.channelCode,body.templateCode,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})
