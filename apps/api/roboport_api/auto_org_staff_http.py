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
from .auto_org_staff import (grant_staff, revoke_staff, list_staff, list_staff_audit,\n    create_staff_invitation, list_staff_invitations, revoke_staff_invitation,\n    accept_staff_invitation, list_staff_invitation_audit, OrgStaffDenied, OrgStaffConflict)

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



class StaffInvitationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targetLogin: str = Field(min_length=3, max_length=128)
    role: str = Field(pattern="^(MANAGER|INTAKE|MECHANIC|WAREHOUSE|CASHIER)$")
    locationId: UUID | None = None
    expiresInDays: int = Field(default=7, strict=True, ge=1, le=30)


class StaffInvitationAcceptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=40, max_length=64, pattern="^[A-Za-z0-9_-]+$")


@router.post("/api/v1/dev/auto/organizations/{org_id}/staff-invitations")
async def create_staff_invite(org_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization = UUID(org_id)
        body = StaffInvitationBody.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = create_staff_invitation(
                db, request.cookies.get("rp_session", ""), organization,
                body.targetLogin, body.role, body.locationId,
                expires_days=body.expiresInDays,
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
        except OrgStaffConflict:
            return _response(request, 409, "CONFLICT")
    # The opaque token is returned exactly once and never persisted in plaintext.
    return JSONResponse(status_code=201, content=result,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/staff-invitations")
def get_staff_invitations(org_id: str, request: Request, limit: int = 100):
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
            items = list_staff_invitations(
                db, request.cookies.get("rp_session", ""), organization, limit=limit
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items": items},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/staff-invitations/{invitation_id}/revoke")
def remove_staff_invitation(org_id: str, invitation_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        organization, ident = UUID(org_id), UUID(invitation_id)
    except (TypeError, ValueError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = revoke_staff_invitation(
                db, request.cookies.get("rp_session", ""), organization, ident
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status": result},
                        headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/staff-invitations/audit")
def get_staff_invitation_audit(org_id: str, request: Request, limit: int = 100):
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
            events = list_staff_invitation_audit(
                db, request.cookies.get("rp_session", ""), organization, limit=limit
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"items": events},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/staff-invitations/accept")
async def accept_staff_invite(request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        body = StaffInvitationAcceptBody.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = accept_staff_invitation(
                db, request.cookies.get("rp_session", ""), body.token
            )
        except OrgStaffDenied:
            return _response(request, 403, "FORBIDDEN")
        except OrgStaffConflict:
            return _response(request, 409, "CONFLICT")
    if result["status"] == "EXPIRED":
        return _response(request, 410, "INVITATION_EXPIRED")
    if result["status"] != "ACCEPTED":
        return _response(request, 409, result["status"])
    return JSONResponse(status_code=201, content=result,
                        headers={"Cache-Control": "no-store"})


class PolicyBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    role:str=Field(pattern="^(MANAGER|INTAKE|MECHANIC|WAREHOUSE|CASHIER)$")
    locationId:UUID|None=None
    userId:UUID|None=None
    documentKind:str=Field(pattern="^(INTAKE_ACT|ESTIMATE|HANDOVER|SUPPLIER_ORDER|FINANCE)$")
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
        # Не доверять дублированным/невалидным курсорам: ошибки без
        # времени/содержимого чужого policy event.
        cursors=request.query_params.getlist("before")
        limits=request.query_params.getlist("limit")
        if len(cursors)>1 or len(limits)>1 or (cursors and not cursors[0]):
            return _response(request,422,"INVALID_INPUT")
        before=UUID(cursors[0]) if cursors else None
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=list_policies(db,request.cookies.get("rp_session",""),
                                 org,limit=limit,before=before)
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
