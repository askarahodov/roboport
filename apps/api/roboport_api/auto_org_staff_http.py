"""Closed, loopback-only organization staff endpoints for synthetic PG17.

No ADMIN bootstrap endpoint. No real personnel or public production access.
"""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_org_staff import grant_staff, revoke_staff, list_staff, OrgStaffDenied, OrgStaffConflict

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
