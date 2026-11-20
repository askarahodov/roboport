"""Закрытый synthetic HTTP API управления филиалами, постами и сменами.

Все изменения требуют живой сессии, CSRF и ADMIN конкретной организации.
"""
from datetime import datetime
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_workshop_admin import (
    WorkshopAdminConflict, WorkshopAdminDenied, WorkshopAdminInvalid,
    cancel_shift, create_bay, create_location, create_shift, disable_bay,
    disable_location, list_workshop, list_workshop_audit,
)

router = APIRouter(include_in_schema=False)


class LocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9_-]{1,31}$")
    timeZone: str = Field(min_length=3, max_length=64)


class BayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=2, max_length=32, pattern=r"^[A-Z0-9][A-Z0-9_-]{1,31}$")


class ShiftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    userId: UUID
    startsAt: datetime
    endsAt: datetime


def _org(value: str):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def _location(value: str):
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None


def _denied(request: Request):
    return _response(request, 403, "FORBIDDEN")


def _invalid(request: Request):
    return _response(request, 422, "INVALID_INPUT")


def _conflict(request: Request):
    return _response(request, 409, "CONFLICT")


@router.get("/api/v1/dev/auto/organizations/{org_id}/workshop")
def get_workshop(org_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization = _org(org_id)
    if organization is None:
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        try:
            data = list_workshop(db, request.cookies.get("rp_session", ""), organization)
        except WorkshopAdminDenied:
            return _denied(request)
    return JSONResponse(content=data, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/workshop/audit")
def get_workshop_audit(org_id: str, request: Request, limit: int = 100):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization = _org(org_id)
    if organization is None or not 1 <= limit <= 100:
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        try:
            items = list_workshop_audit(
                db, request.cookies.get("rp_session", ""), organization, limit
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
    return JSONResponse(content={"items": items}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations")
async def post_location(org_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization = _org(org_id)
    if organization is None:
        return _invalid(request)
    try:
        body = LocationBody.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            ident = create_location(
                db, request.cookies.get("rp_session", ""), organization,
                body.code, body.timeZone,
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(status_code=201, content={"locationId": str(ident)},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{location_id}/disable")
def post_disable_location(org_id: str, location_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization, location = _org(org_id), _location(location_id)
    if organization is None or location is None:
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            result = disable_location(
                db, request.cookies.get("rp_session", ""), organization, location
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(content={"status": result}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{location_id}/bays")
async def post_bay(org_id: str, location_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization, location = _org(org_id), _location(location_id)
    if organization is None or location is None:
        return _invalid(request)
    try:
        body = BayBody.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            ident = create_bay(
                db, request.cookies.get("rp_session", ""), organization,
                location, body.code,
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(status_code=201, content={"bayId": str(ident)},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{location_id}/bays/{bay_id}/disable")
def post_disable_bay(org_id: str, location_id: str, bay_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization, location, bay = _org(org_id), _location(location_id), _location(bay_id)
    if organization is None or location is None or bay is None:
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            result = disable_bay(
                db, request.cookies.get("rp_session", ""), organization, location, bay
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(content={"status": result}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{location_id}/shifts")
async def post_shift(org_id: str, location_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization, location = _org(org_id), _location(location_id)
    if organization is None or location is None:
        return _invalid(request)
    try:
        body = ShiftBody.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            ident = create_shift(
                db, request.cookies.get("rp_session", ""), organization,
                location, body.userId, body.startsAt, body.endsAt,
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(status_code=201, content={"shiftId": str(ident)},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{location_id}/shifts/{shift_id}/cancel")
def post_cancel_shift(org_id: str, location_id: str, shift_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    organization, location, shift = _org(org_id), _location(location_id), _location(shift_id)
    if organization is None or location is None or shift is None:
        return _invalid(request)
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _denied(request)
        try:
            result = cancel_shift(
                db, request.cookies.get("rp_session", ""), organization, location, shift
            )
        except WorkshopAdminDenied:
            return _denied(request)
        except WorkshopAdminInvalid:
            return _invalid(request)
        except WorkshopAdminConflict:
            return _conflict(request)
    return JSONResponse(content={"status": result}, headers={"Cache-Control": "no-store"})
