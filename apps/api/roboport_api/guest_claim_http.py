"""Synthetic HTTPS-only claim flow, never trusts user, customer or staff IDs."""
import os
from urllib.parse import urlsplit
from uuid import UUID
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
import psycopg
from pydantic import BaseModel,Field,ValidationError
from .auth_http import _mutation_ok, _origin_ok
from .guest_claim import issue_proof,redeem_proof,review_claim,ClaimRejected
from .session_lookup import session_subject

router=APIRouter(include_in_schema=False)

def _dsn(req):
    raw=os.getenv("ROBOport_DATABASE_URL","")
    try: p=urlsplit(raw)
    except ValueError: return None
    if (os.getenv("ROBOport_SYNTHETIC_ACCESS_API")!="1"
        or req.url.scheme!="https" or req.url.hostname not in ("localhost","127.0.0.1")
        or p.scheme not in ("postgresql","postgresql+psycopg")
        or p.hostname!="127.0.0.1" or p.port is None):
        return None
    return raw.replace("postgresql+psycopg://","postgresql://",1)

def _err(req,status,code):
    return JSONResponse(status_code=status,content={"code":code,"requestId":req.state.request_id},
       headers={"Cache-Control":"no-store"})

class ClaimPayload(BaseModel):
    proof:str=Field(min_length=20,max_length=200)

class ReviewPayload(BaseModel):
    approve:bool

@router.post("/api/v1/dev/guest-claims/tickets/{ticket_id}/issue")
def issue(ticket_id:str,request:Request):
    dsn=_dsn(request)
    if dsn is None:return _err(request,404,"NOT_FOUND")
    try: ticket=UUID(ticket_id)
    except ValueError:return _err(request,404,"NOT_FOUND")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):return _err(request,403,"FORBIDDEN")
        try: ident,proof=issue_proof(conn,request.cookies.get("rp_session",""),ticket)
        except (ClaimRejected,psycopg.errors.UniqueViolation):return _err(request,403,"FORBIDDEN")
    # Proof is sent only to assigned staff via HTTPS response; NOT available on public intake.
    return JSONResponse(content={"claimId":str(ident),"proof":proof},
        headers={"Cache-Control":"no-store","Pragma":"no-cache"})

@router.post("/api/v1/dev/guest-claims/{claim_id}/redeem")
async def redeem(claim_id:str,request:Request):
    dsn=_dsn(request)
    if dsn is None:return _err(request,404,"NOT_FOUND")
    try:
        claim=UUID(claim_id)
        payload=ClaimPayload.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError):return _err(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):return _err(request,403,"FORBIDDEN")
        try: outcome=redeem_proof(conn,request.cookies.get("rp_session",""),claim,payload.proof)
        except ClaimRejected:return _err(request,403,"FORBIDDEN")
    return JSONResponse(content={"status":outcome},headers={"Cache-Control":"no-store"})

@router.post("/api/v1/dev/guest-claims/{claim_id}/review")
async def review(claim_id:str,request:Request):
    dsn=_dsn(request)
    if dsn is None:return _err(request,404,"NOT_FOUND")
    try:
        claim=UUID(claim_id)
        payload=ReviewPayload.model_validate(await request.json())
    except (ValueError,ValidationError,TypeError):return _err(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request,conn):return _err(request,403,"FORBIDDEN")
        try: outcome=review_claim(conn,request.cookies.get("rp_session",""),claim,payload.approve)
        except ClaimRejected:return _err(request,403,"FORBIDDEN")
    return JSONResponse(content={"status":outcome},headers={"Cache-Control":"no-store"})
