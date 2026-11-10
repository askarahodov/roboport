"""Closed-loopback synthetic public widget and staff/customer handoff endpoints.

Anonymous operations require same-origin JSON, a one-use dev challenge and
branch-level quotas. Customer mutations require live cookie + CSRF, and never
accept VIN, arbitrary customer id or direct unverified vehicle ownership.
"""
from datetime import datetime
from uuid import UUID
from typing import Literal
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,Field,ConfigDict,ValidationError,StrictBool

from .auto_vehicle_claim_http import _request_dsn,_response
from .auth_http import _mutation_ok
from .auto_public_booking import (
    list_offers,publish_offer,revoke_offer,slot_candidates,challenge,submit_request,status,
    claim_request,withdraw_marketing,staff_queue,fulfill_request,
    PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict,
)

router=APIRouter(include_in_schema=False)
PUBLIC="/api/v1/dev/auto/public-booking"
STAFF="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/public-booking"


class SafeBody(BaseModel):
    model_config=ConfigDict(extra="forbid")


class Material(SafeBody):
    skuId:UUID
    kind:Literal["PART","CONSUMABLE"]
    quantity:int=Field(strict=True,ge=1,le=1_000_000_000)


class OfferBody(SafeBody):
    bayId:UUID
    serviceId:UUID
    policyVersion:str=Field(pattern="^DEV-CONSENT-[0-9]{4}$")
    requestKey:UUID
    materials:list[Material]=Field(default_factory=list,max_length=8)


class ChallengeBody(SafeBody):
    offerId:UUID


class SubmitBody(SafeBody):
    offerId:UUID
    requestKey:UUID
    challengeId:UUID
    answer:int=Field(strict=True,ge=0,le=99)
    startsAt:datetime
    endsAt:datetime
    processingConsent:StrictBool
    marketingConsent:StrictBool=False


class ReceiptBody(SafeBody):
    receiptKey:UUID


class ClaimBody(ReceiptBody):
    claimKey:UUID
    customerLinkId:UUID
    processingConsent:StrictBool


class WithdrawBody(ReceiptBody):
    withdrawKey:UUID


class FulfillBody(SafeBody):
    requestKey:UUID


class RevokeBody(SafeBody):
    requestKey:UUID


def _parse(body,model):
    try:
        return model.model_validate(body)
    except (ValidationError,TypeError,ValueError,UnicodeDecodeError):
        raise PublicBookingInvalid("INVALID_INPUT") from None


async def _read_body(request,model):
    try:
        return _parse(await request.json(),model)
    except (TypeError,ValueError,UnicodeDecodeError):
        raise PublicBookingInvalid("INVALID_INPUT") from None


def _same_origin(request):
    # Do not make a public authenticated browser endpoint CSRF-capable.
    # HTTP/Host/proxy spoofing is blocked by the parent /dev middleware,
    # which only allows direct HTTPS loopback.
    expected=str(request.base_url).rstrip("/")
    return (request.headers.get("origin")==expected
            and request.headers.get("sec-fetch-site","same-origin") in
            ("same-origin","none")
            and request.headers.get("content-type","").split(";")[0].strip().lower()
                =="application/json")


def _error(request,exc):
    if isinstance(exc,PublicBookingConflict):
        code=str(exc)
        return _response(request,429 if code=="RATE_LIMITED" else 409,code)
    if isinstance(exc,PublicBookingInvalid):
        return _response(request,422,"INVALID_INPUT")
    return _response(request,403,"FORBIDDEN")


def _json(data,status_code=200):
    return JSONResponse(status_code=status_code,content=data,
                        headers={"Cache-Control":"no-store"})


def _scope(request,org_id,loc_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return dsn,org,loc


@router.get(PUBLIC+"/offers")
def public_offers(request:Request,organizationId:str,locationId:str):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(organizationId),UUID(locationId)
        with psycopg.connect(dsn) as db:
            data=list_offers(db,org,loc)
    except (TypeError,ValueError,PublicBookingDenied,PublicBookingInvalid,
            PublicBookingConflict) as exc:
        return _error(request,exc if isinstance(exc,(PublicBookingDenied,
                       PublicBookingInvalid,PublicBookingConflict))
                      else PublicBookingInvalid("INVALID_INPUT"))
    return _json(data)


@router.get(PUBLIC+"/slots")
def public_slots(request:Request,offerId:str,localDay:str):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        with psycopg.connect(dsn) as db:
            result=slot_candidates(db,UUID(offerId),localDay)
    except (ValueError,TypeError,PublicBookingDenied,
            PublicBookingInvalid,PublicBookingConflict) as exc:
        return _error(request,exc if isinstance(exc,(PublicBookingDenied,
                     PublicBookingInvalid,PublicBookingConflict))
                      else PublicBookingInvalid("INVALID_INPUT"))
    return _json(result)


@router.post(PUBLIC+"/challenges")
async def public_challenge(request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if not _same_origin(request):
        return _response(request,403,"FORBIDDEN")
    try:
        body=await _read_body(request,ChallengeBody)
        with psycopg.connect(dsn) as db:
            result=challenge(db,body.offerId)
    except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
        return _error(request,exc)
    return _json(result,201)


@router.post(PUBLIC+"/requests")
async def public_submit(request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if not _same_origin(request):
        return _response(request,403,"FORBIDDEN")
    try:
        body=await _read_body(request,SubmitBody)
        with psycopg.connect(dsn) as db:
            result=submit_request(db,body.offerId,request_key=body.requestKey,
                  challenge_id=body.challengeId,answer=body.answer,
                  start=body.startsAt,end=body.endsAt,
                  processing=body.processingConsent,marketing=body.marketingConsent)
    except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
        return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)


@router.post(PUBLIC+"/requests/{request_id}/status")
async def public_request_status(request_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if not _same_origin(request):
        return _response(request,403,"FORBIDDEN")
    try:
        body=await _read_body(request,ReceiptBody)
        with psycopg.connect(dsn) as db:
            result=status(db,UUID(request_id),body.receiptKey)
    except (ValueError,PublicBookingDenied,PublicBookingInvalid,
            PublicBookingConflict) as exc:
        return _error(request,exc if isinstance(exc,(PublicBookingDenied,
            PublicBookingInvalid,PublicBookingConflict))
            else PublicBookingInvalid("INVALID_INPUT"))
    return _json(result)


@router.post(PUBLIC+"/requests/{request_id}/claim")
async def customer_claim(request_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        body=await _read_body(request,ClaimBody)
        ident=UUID(request_id)
    except (ValueError,PublicBookingInvalid):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=claim_request(db,request.cookies.get("rp_session",""),ident,
                  body.receiptKey,body.customerLinkId,body.claimKey,
                  body.processingConsent)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)


@router.post(PUBLIC+"/requests/{request_id}/withdraw")
async def customer_withdraw(request_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        body=await _read_body(request,WithdrawBody)
        ident=UUID(request_id)
    except (ValueError,PublicBookingInvalid):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=withdraw_marketing(db,request.cookies.get("rp_session",""),
                                     ident,body.receiptKey,body.withdrawKey)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)


@router.get(STAFF+"/offers")
def staff_offers(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    from .auto_public_booking import _staff
    with psycopg.connect(dsn) as db:
        try:
            with db.transaction():
                _staff(db,request.cookies.get("rp_session",""),org,loc,("MANAGER",))
            result=list_offers(db,org,loc)
        except (PublicBookingDenied,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result)


@router.post(STAFF+"/offers")
async def staff_publish(org_id:str,loc_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    try:
        body=await _read_body(request,OfferBody)
    except PublicBookingInvalid as exc:
        return _error(request,exc)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=publish_offer(db,request.cookies.get("rp_session",""),
                org,loc,bay=body.bayId,service=body.serviceId,
                materials=tuple((m.skuId,m.kind,m.quantity) for m in body.materials),
                policy=body.policyVersion,key=body.requestKey)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)


@router.get(STAFF+"/queue")
def staff_requests(org_id:str,loc_id:str,request:Request,page:int=0):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    with psycopg.connect(dsn) as db:
        try:
            result=staff_queue(db,request.cookies.get("rp_session",""),org,loc,page=page)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result)


@router.post(STAFF+"/requests/{request_id}/fulfill")
async def staff_fulfill(org_id:str,loc_id:str,request_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    try:
        ident=UUID(request_id)
        body=await _read_body(request,FulfillBody)
    except (ValueError,PublicBookingInvalid):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=fulfill_request(db,request.cookies.get("rp_session",""),
                  org,loc,ident,body.requestKey)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)


@router.post(STAFF+"/offers/{offer_id}/revoke")
async def staff_revoke(org_id:str,loc_id:str,offer_id:str,request:Request):
    scoped=_scope(request,org_id,loc_id)
    if isinstance(scoped,JSONResponse):
        return scoped
    dsn,org,loc=scoped
    try:
        ident=UUID(offer_id)
        body=await _read_body(request,RevokeBody)
    except (ValueError,PublicBookingInvalid):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=revoke_offer(db,request.cookies.get("rp_session",""),
                                org,loc,ident,body.requestKey)
        except (PublicBookingDenied,PublicBookingInvalid,PublicBookingConflict) as exc:
            return _error(request,exc)
    return _json(result,200 if result["replayed"] else 201)
