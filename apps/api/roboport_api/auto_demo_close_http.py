"""Closed HTTPS dev-only synthetic reconciliation and pre-handover documents."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,ValidationError
from .auto_vehicle_claim_http import _request_dsn,_response
from .auth_http import _mutation_ok
from .auto_demo_close import (
    DemoCloseDenied,DemoCloseConflict,read_demo_close,simulate_cash,
    release_unused_reserve,seal_handover_preview,
)

from .auto_document_lifecycle import (
    record_document_event,DocumentLifecycleDenied,
    DocumentLifecycleInvalid,DocumentLifecycleConflict,
)

router=APIRouter(include_in_schema=False)
prefix="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/demo-close"


class RequestKey(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


class CashEvent(RequestKey):
    kind:str=Field(pattern="^(SIM_PAYMENT|SIM_REFUND)$")
    amountMinor:int=Field(ge=1,le=10**12)


class ReserveRelease(RequestKey):
    skuId:UUID
    quantity:int=Field(ge=1,le=1_000_000_000)


class DocumentEvent(RequestKey):
    action:str=Field(pattern="^(POSTED|CLIENT_ACK|CLIENT_DISPUTE|CLOSED_TECHNICAL)$")
    previewId:UUID
    previewDigest:str=Field(pattern="^[0-9a-f]{64}$")
    intakeActId:UUID
    intakeDigest:str=Field(pattern="^[0-9a-f]{64}$")


def _scope(request,org_id,loc_id,order_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id),UUID(order_id)
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")


def _error(request,exc):
    if isinstance(exc,DemoCloseConflict):
        return _response(request,409,str(exc))
    if str(exc)=="INVALID_INPUT":
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


@router.get(prefix)
def read(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc,order=scoped
    with psycopg.connect(dsn) as db:
        try:
            state=read_demo_close(db,request.cookies.get("rp_session",""),org,loc,order)
        except (DemoCloseDenied,DemoCloseConflict) as exc:
            return _error(request,exc)
    return JSONResponse(content=state,headers={"Cache-Control":"no-store"})


async def _write(request,org_id,loc_id,order_id,action):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc,order=scoped
    try:
        body=await request.json()
        data=(CashEvent if action=="cash" else
              ReserveRelease if action=="release" else RequestKey).model_validate(body)
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            token=request.cookies.get("rp_session","")
            if action=="cash":
                outcome=simulate_cash(db,token,org,loc,order,
                    request_key=data.requestKey,kind=data.kind,amount_minor=data.amountMinor)
            elif action=="release":
                outcome=release_unused_reserve(db,token,org,loc,order,
                    request_key=data.requestKey,sku_id=data.skuId,quantity=data.quantity)
            else:
                outcome=seal_handover_preview(db,token,org,loc,order,
                    request_key=data.requestKey)
        except (DemoCloseDenied,DemoCloseConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=200 if outcome.get("replayed") else 201,
                        content=outcome,headers={"Cache-Control":"no-store"})


@router.post(prefix+"/simulated-cash")
async def cash(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"cash")


@router.post(prefix+"/unused-reserve-release")
async def release(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"release")


@router.post(prefix+"/seal-preview")
async def preview(org_id:str,loc_id:str,order_id:str,request:Request):
    return await _write(request,org_id,loc_id,order_id,"preview")


@router.post(prefix+"/document-events")
async def document_event(org_id:str,loc_id:str,order_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id,order_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc,order=scoped
    try:
        body=DocumentEvent.model_validate(await request.json())
    except (ValidationError,ValueError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            outcome=record_document_event(
                db,request.cookies.get("rp_session",""),org,loc,order,
                request_key=body.requestKey,action=body.action,
                preview_id=body.previewId,preview_digest=body.previewDigest,
                intake_act_id=body.intakeActId,intake_digest=body.intakeDigest)
        except DocumentLifecycleConflict as exc:
            return _response(request,409,str(exc))
        except DocumentLifecycleInvalid:
            return _response(request,422,"INVALID_INPUT")
        except DocumentLifecycleDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(status_code=200 if outcome["replayed"] else 201,
                        content=outcome,headers={"Cache-Control":"no-store"})
