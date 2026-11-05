"""Private synthetic CRM contact choices; no outbound delivery or legal consent."""
from uuid import UUID

import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,ValidationError,Field

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_crm_leads_http import _context,_error
from .auto_crm_leads import LeadDenied,LeadConflict
from .auto_crm_contact_choices import (
    read_customer_choices,change_customer_choice,read_staff_choices,
)

router=APIRouter(include_in_schema=False)


class ChoiceBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:str
    enabled:bool=Field(strict=True)
    expectedRevision:int=Field(strict=True,ge=0,lt=2147483647)
    requestKey:UUID


@router.get("/api/v1/dev/account/crm-contact-preferences")
def customer_contact_preferences(request:Request):
    config=_request_dsn(request)
    if isinstance(config,JSONResponse):
        return config
    with psycopg.connect(config) as db:
        try:
            result=read_customer_choices(db,request.cookies.get("rp_session",""))
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/account/crm-contact-preferences")
async def update_customer_contact_preferences(request:Request):
    config=_request_dsn(request)
    if isinstance(config,JSONResponse):
        return config
    try:
        body=ChoiceBody.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(config) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=change_customer_choice(
                db,request.cookies.get("rp_session",""),
                body.channelCode,body.enabled,body.expectedRevision,body.requestKey,
            )
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,
                        status_code=200 if result["replayed"] else 201,
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/leads/{lead_id}/contact-preferences")
def staff_contact_preferences(org_id:str,loc_id:str,lead_id:str,request:Request):
    context=_context(request,org_id,loc_id,lead_id)
    if isinstance(context,JSONResponse):
        return context
    dsn,(org,loc,lead)=context
    with psycopg.connect(dsn) as db:
        try:
            result=read_staff_choices(db,request.cookies.get("rp_session",""),
                                      org,loc,lead)
        except (LeadDenied,LeadConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
