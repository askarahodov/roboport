"""Закрытое делегирование только INTAKE на конкретную машину."""
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim import VehicleClaimRejected
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_intake_delegation import AssignmentConflict, grant_intake, revoke_intake, list_intake

router = APIRouter(include_in_schema=False)


class GrantPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: UUID


@router.get("/api/v1/dev/auto/vehicles/{vehicle_id}/intake-assignments")
def active_intake_assignments(vehicle_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        ident = UUID(vehicle_id)
    except ValueError:
        return _response(request, 404, "NOT_FOUND")
    with psycopg.connect(config) as conn:
        try:
            items = list_intake(conn, request.cookies.get("rp_session", ""), ident)
        except VehicleClaimRejected:
            return _response(request, 404, "NOT_FOUND")
    return JSONResponse(content={"items": items}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/vehicles/{vehicle_id}/intake-assignments")
async def create_intake_assignment(vehicle_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        ident = UUID(vehicle_id)
        body = GrantPayload.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            assigned = grant_intake(
                conn, request.cookies.get("rp_session", ""), ident, body.userId,
            )
        except AssignmentConflict:
            return _response(request, 409, "CONFLICT")
        except VehicleClaimRejected:
            return _response(request, 404, "NOT_FOUND")
    return JSONResponse(status_code=201, content={
        "assignmentId": str(assigned), "status": "ACTIVE",
    }, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/vehicles/{vehicle_id}/intake-assignments/{assignment_id}/revoke")
def revoke_intake_assignment(vehicle_id: str, assignment_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        vehicle = UUID(vehicle_id)
        assignment = UUID(assignment_id)
    except ValueError:
        return _response(request, 404, "NOT_FOUND")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = revoke_intake(
                conn, request.cookies.get("rp_session", ""), vehicle, assignment,
            )
        except VehicleClaimRejected:
            return _response(request, 404, "NOT_FOUND")
    return JSONResponse(content={"status": result}, headers={"Cache-Control": "no-store"})
