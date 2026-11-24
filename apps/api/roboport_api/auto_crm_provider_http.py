"""Closed dev provider rehearsal HTTP contract."""
from uuid import UUID
from typing import Literal
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_crm_leads import LeadDenied,LeadConflict
from .auto_crm_leads_http import _error
from .auto_crm_provider import provider_attempt,ingest_call,call_history
from .auto_crm_consent import read_customer_consent,change_customer_consent,read_staff_consent,create_mock_message,list_mock_messages

router=APIRouter(include_in_schema=False)
ROOT="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm"
LEAD=ROOT+"/leads/{lead_id}"
CONSENT="/api/v1/dev/account/crm-provider-consent"
Channel=Literal["CALL","EMAIL","SMS","MESSENGER"]


class Consent(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:Channel
    enabled:bool=Field(strict=True)
    expectedRevision:int=Field(strict=True,ge=0,lt=2147483647)
    requestKey:UUID


class Message(BaseModel):
    model_config=ConfigDict(extra="forbid")
    channelCode:Channel
    templateCode:Literal["BOOKING_REMINDER","SERVICE_UPDATE","POST_SERVICE_REVIEW"]
    requestKey:UUID


class Attempt(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    outcome:Literal["ACCEPTED","TIMEOUT","RATE_LIMIT","REJECTED"]


class Call(BaseModel):
    model_config=ConfigDict(extra="forbid")
    customerLinkId:UUID
    providerEventId:UUID
    disposition:Literal["ANSWERED","MISSED"]


def _run(request,ids,fn,mutation=False):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        scope=tuple(UUID(x) for x in ids)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as db:
            if mutation and not _mutation_ok(request,db):
                return _response(request,403,"FORBIDDEN")
            result=fn(db,request.cookies.get("rp_session",""),*scope)
    except (LeadDenied,LeadConflict) as exc:
        return _error(request,exc)
    except (psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure):
        return _response(request,409,"CONFLICT")
    return JSONResponse(content=result,status_code=201 if mutation and not result.get("replayed") else 200,
                        headers={"Cache-Control":"no-store"})


async def _post(request,ids,model,fn):
    try:
        body=model.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    return _run(request,ids,lambda db,t,*scope:fn(db,t,*scope,body),True)


@router.get(CONSENT)
def read_consent(request:Request):
    return _run(request,(),lambda db,t:read_customer_consent(db,t,extended=True))


@router.post(CONSENT)
async def update_consent(request:Request):
    return await _post(request,(),Consent,lambda db,t,b:change_customer_consent(
        db,t,b.channelCode,b.enabled,b.expectedRevision,b.requestKey,extended=True))


@router.get(LEAD+"/provider-consent")
def staff_consent(org_id:str,loc_id:str,lead_id:str,request:Request):
    return _run(request,(org_id,loc_id,lead_id),lambda db,t,*s:read_staff_consent(db,t,*s,extended=True))


@router.get(LEAD+"/provider-messages")
def messages(org_id:str,loc_id:str,lead_id:str,request:Request,cursor:str|None=None):
    if len(request.query_params.getlist("cursor"))>1:
        return _response(request,422,"INVALID_INPUT")
    return _run(request,(org_id,loc_id,lead_id),lambda db,t,*s:list_mock_messages(db,t,*s,cursor,extended=True))


@router.post(LEAD+"/provider-messages")
async def message(org_id:str,loc_id:str,lead_id:str,request:Request):
    return await _post(request,(org_id,loc_id,lead_id),Message,lambda db,t,o,l,i,b:
        create_mock_message(db,t,o,l,i,b.requestKey,b.channelCode,b.templateCode,extended=True))


@router.get(LEAD+"/provider-messages/{message_id}/attempts")
def attempts(org_id:str,loc_id:str,lead_id:str,message_id:str,request:Request):
    return _run(request,(org_id,loc_id,lead_id,message_id),provider_attempt)


@router.post(LEAD+"/provider-messages/{message_id}/attempts")
async def attempt(org_id:str,loc_id:str,lead_id:str,message_id:str,request:Request):
    return await _post(request,(org_id,loc_id,lead_id,message_id),Attempt,lambda db,t,o,l,i,m,b:
        provider_attempt(db,t,o,l,i,m,key=b.requestKey,outcome=b.outcome))


@router.post(ROOT+"/mock-calls")
async def call(org_id:str,loc_id:str,request:Request):
    return await _post(request,(org_id,loc_id),Call,lambda db,t,o,l,b:
        ingest_call(db,t,o,l,b.customerLinkId,b.providerEventId,b.disposition))


@router.get(LEAD+"/mock-calls")
def calls(org_id:str,loc_id:str,lead_id:str,request:Request):
    return _run(request,(org_id,loc_id,lead_id),call_history)
