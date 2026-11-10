"""Closed, loopback-only organization staff endpoints for synthetic PG17.

No ADMIN bootstrap endpoint. No real personnel or public production access.
"""
from uuid import UUID
from datetime import datetime
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field, StrictBool

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_org_staff import grant_staff, revoke_staff, list_staff, list_staff_audit, OrgStaffDenied, OrgStaffConflict

from .auto_document_access import (
    publish_policy,list_policies,preview_employee_document_access,DocumentPolicyDenied,
    DocumentPolicyInvalid,DocumentPolicyConflict,
)

router = APIRouter(include_in_schema=False)


class StaffGrantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: UUID
    role: str
    locationId: UUID | None = None


@router.post("/api/v1/dev/auto/organizations/{org_id}/staff")
async def create_staff(org_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization = UUID(org_id)
        body = StaffGrantBody.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            ident = grant_staff(db, request.cookies.get("rp_session", ""),
                                organization, body.userId, body.role, body.locationId)
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
        except OrgStaffConflict:
            return _response(request, 409, "CONFLICT")
    return JSONResponse(status_code=201, content={"grantId": str(ident)},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/staff/{grant_id}/revoke")
def remove_staff(org_id: str, grant_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization, ident = UUID(org_id), UUID(grant_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = revoke_staff(db, request.cookies.get("rp_session", ""),
                                  organization, ident)
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status": result}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/staff")
def get_staff(org_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization = UUID(org_id)
    except (TypeError, ValueError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            items = list_staff(db, request.cookies.get("rp_session", ""), organization)
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items": items}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/staff/audit")
def get_staff_audit(org_id: str, request: Request, limit: int = 100):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization = UUID(org_id)
    except (TypeError, ValueError):
        return _response(request, 422, "INVALID_INPUT")
    if not 1 <= limit <= 100:
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            events = list_staff_audit(
                db, request.cookies.get("rp_session", ""), organization, limit=limit
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items": events}, headers={"Cache-Control": "no-store"})


class PolicyBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    role:str=Field(pattern="^(MANAGER|INTAKE|MECHANIC|WAREHOUSE)$")
    locationId:UUID|None=None
    userId:UUID|None=None
    documentKind:str=Field(pattern="^(INTAKE_ACT|ESTIMATE|HANDOVER|SUPPLIER_ORDER)$")
    capability:str=Field(pattern="^(READ|CREATE|EDIT|WRITE|CLOSE|PRICE_SALES|PRICE_PURCHASE|DISCOUNT)$")
    allowed:StrictBool
    notBefore:datetime|None=None


@router.get("/api/v1/dev/auto/organizations/{org_id}/access-policies")
def get_document_policies(org_id:str,request:Request,limit:int=100):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=list_policies(db,request.cookies.get("rp_session",""),
                                 org,limit=limit)
        except DocumentPolicyInvalid:
            return _response(request,422,"INVALID_INPUT")
        except DocumentPolicyDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/access-policies")
async def set_document_policy(org_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        body=PolicyBody.model_validate(await request.json())
    except (TypeError,ValueError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=publish_policy(
                db,request.cookies.get("rp_session",""),org,
                request_key=body.requestKey,role=body.role,loc=body.locationId,
                user=body.userId,document=body.documentKind,cap=body.capability,
                allowed=body.allowed,not_before=body.notBefore)
        except DocumentPolicyInvalid:
            return _response(request,422,"INVALID_INPUT")
        except DocumentPolicyDenied:
            return _response(request,403,"FORBIDDEN")
        except DocumentPolicyConflict as exc:
            return _response(request,409,str(exc))
    return JSONResponse(status_code=200 if result["replayed"] else 201,
                        content={"syntheticOnly":True,**result},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/access-policies/effective")
def get_effective_document_policy(org_id:str,request:Request,
                                  locationId:str,userId:str,documentKind:str):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc,user=UUID(org_id),UUID(locationId),UUID(userId)
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=preview_employee_document_access(
                db,request.cookies.get("rp_session",""),
                org,loc,user,documentKind)
        except DocumentPolicyInvalid:
            return _response(request,422,"INVALID_INPUT")
        except DocumentPolicyDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
