"""Private HTTPS synthetic treasury: no payment processor, webhooks or fiscalization."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,StrictInt,ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_treasury import (
  TreasuryDenied,TreasuryInvalid,TreasuryConflict,
  create_account,list_accounts,read_order,record_event,close_day,report,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/treasury"


class Key(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


class AccountBody(Key):
    code:str=Field(pattern="^[A-Z0-9_-]{2,32}$")
    label:str=Field(min_length=3,max_length=80)
    kind:str=Field(pattern="^(CASH_SIM|BANK_SIM)$")


class EventBody(Key):
    accountId:UUID
    kind:str=Field(pattern="^(ADVANCE|PAYMENT|APPLY_ADVANCE|REFUND_ADVANCE|REFUND_PAYMENT)$")
    amountMinor:StrictInt=Field(ge=1,le=10**12)
    reason:str=Field(min_length=3,max_length=160)


class CloseBody(Key):
    businessDay:str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


def _scope(request,org_id,loc_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id)
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")


def _reject(request,error):
    if isinstance(error,TreasuryConflict):
        return _response(request,409,str(error))
    if isinstance(error,TreasuryInvalid):
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


def _respond(data):
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})


async def _write(request,org_id,loc_id,operation,*,order_id=None,account_id=None):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    try:
        order=UUID(order_id) if order_id is not None else None
        account=UUID(account_id) if account_id is not None else None
        body=(AccountBody if operation=="account" else
              EventBody if operation=="event" else CloseBody).model_validate(await request.json())
    except (TypeError,ValueError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            token=request.cookies.get("rp_session","")
            if operation=="account":
                state=create_account(db,token,org,loc,request_key=body.requestKey,
                    code=body.code,label=body.label,kind=body.kind)
            elif operation=="event":
                state=record_event(db,token,org,loc,order,
                    account_id=body.accountId,request_key=body.requestKey,
                    kind=body.kind,amount_minor=body.amountMinor,reason=body.reason)
            else:
                state=close_day(db,token,org,loc,account,
                    request_key=body.requestKey,day=body.businessDay)
        except (TreasuryDenied,TreasuryInvalid,TreasuryConflict) as exc:
            return _reject(request,exc)
    return JSONResponse(status_code=200 if state.get("replayed") else 201,
                        content=state,headers={"Cache-Control":"no-store"})


@router.get(prefix+"/accounts")
def accounts(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):return scoped
    dsn,org,loc=scoped
    with psycopg.connect(dsn) as db:
        try:result=list_accounts(db,request.cookies.get("rp_session",""),org,loc)
        except (TreasuryDenied,TreasuryInvalid,TreasuryConflict) as exc:
            return _reject(request,exc)
    return _respond(result)


@router.post(prefix+"/accounts")
async def open_account(org_id:str,loc_id:str,request:Request):
    return await _write(request,org_id,loc_id,"account")


@router.get(prefix+"/orders/{order_id}")
def order_view(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):return scoped
    dsn,org,loc=scoped
    try:order=UUID(order_id)
    except (TypeError,ValueError):return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:state=read_order(db,request.cookies.get("rp_session",""),org,loc,order)
        except (TreasuryDenied,TreasuryInvalid,TreasuryConflict) as exc:
            return _reject(request,exc)
    return _respond(state)


@router.post(prefix+"/orders/{order_id}/events")
async def write_event(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,"event",order_id=order_id)


@router.post(prefix+"/accounts/{account_id}/close-day")
async def close_account_day(org_id:str,loc_id:str,account_id:str,request:Request):
    return await _write(request,org_id,loc_id,"close",account_id=account_id)


@router.get(prefix+"/report")
def read_report(org_id:str,loc_id:str,request:Request,
                fromDay:str,toDay:str):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):return scoped
    dsn,org,loc=scoped
    with psycopg.connect(dsn) as db:
        try:result=report(db,request.cookies.get("rp_session",""),org,loc,
                          from_day=fromDay,to_day=toDay)
        except (TreasuryDenied,TreasuryInvalid,TreasuryConflict) as exc:
            return _reject(request,exc)
    return _respond(result)
