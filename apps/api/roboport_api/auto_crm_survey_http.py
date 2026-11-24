"""Closed HTTPS survey API, all mutations require session and CSRF."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal

from .auto_vehicle_claim_http import _request_dsn, _response
from .auth_http import _mutation_ok
from .auto_crm_leads import LeadDenied, LeadConflict
from .auto_crm_survey import staff_survey, customer_survey, survey_report

router=APIRouter(include_in_schema=False)
ROOT="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
STAFF=ROOT+"/crm/leads/{lead_id}/survey"
CUSTOMER=ROOT+"/orders/{order_id}/survey/customer"


class StaffBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    action:Literal["INVITE","CANCEL"]


class AnswerBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    rating:int=Field(strict=True,ge=1,le=5)
    reasonCode:Literal["QUALITY","TIMING","COMMUNICATION","NONE","OTHER"]


def _execute(request,ids,operation,mutation=False):
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
            result=operation(db,request.cookies.get("rp_session",""),*scope)
    except LeadDenied as exc:
        return _response(request,422 if str(exc)=="INVALID_INPUT" else 403,
                         "INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN")
    except (LeadConflict,psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure):
        return _response(request,409,"CONFLICT")
    return JSONResponse(content=result,status_code=201 if mutation and not result.get("replayed") else 200,
                        headers={"Cache-Control":"no-store"})


@router.get(STAFF)
def staff_read(org_id:str,loc_id:str,lead_id:str,request:Request):
    return _execute(request,(org_id,loc_id,lead_id),staff_survey)


@router.post(STAFF)
async def staff_write(org_id:str,loc_id:str,lead_id:str,request:Request):
    try:
        data=StaffBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    return _execute(request,(org_id,loc_id,lead_id),lambda db,t,*s:
        staff_survey(db,t,*s,key=data.requestKey,action=data.action),True)


@router.get(CUSTOMER)
def customer_read(org_id:str,loc_id:str,order_id:str,request:Request):
    return _execute(request,(org_id,loc_id,order_id),customer_survey)


@router.post(CUSTOMER)
async def customer_answer(org_id:str,loc_id:str,order_id:str,request:Request):
    try:
        data=AnswerBody.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError):
        return _response(request,422,"INVALID_INPUT")
    return _execute(request,(org_id,loc_id,order_id),lambda db,t,*s:
        customer_survey(db,t,*s,key=data.requestKey,rating=data.rating,reason=data.reasonCode),True)


@router.get(ROOT+"/crm/survey-report")
def report(org_id:str,loc_id:str,request:Request,fromDay:str,toDay:str):
    if any(len(request.query_params.getlist(k))!=1 for k in ("fromDay","toDay")):
        return _response(request,422,"INVALID_INPUT")
    return _execute(request,(org_id,loc_id),lambda db,t,*s:survey_report(db,t,*s,fromDay,toDay))
