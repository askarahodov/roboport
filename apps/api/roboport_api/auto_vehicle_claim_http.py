"""Синтетический клиентско-сотруднический процесс подтверждения автомобиля.

Публичный API и обработка реальных доказательств запрещены до release gate.
"""
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field

from .auth_http import _mutation_ok
from .auto_create_idempotency import validated_key, AutoIdempotencyConflict
from .auto_vehicle_http import _enabled_dsn, _not_found
from .auto_vehicle_claim import (
    VehicleClaimRejected, issue_auto_proof, redeem_auto_proof,
    review_auto_claim, revoke_auto_link,
)

router = APIRouter(include_in_schema=False)


class ProofPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proof: str = Field(min_length=20, max_length=200)


class DecisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


def _response(request: Request, status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"code": code, "requestId": request.state.request_id},
        headers={"Cache-Control": "no-store"},
    )


def _request_dsn(request: Request) -> str | JSONResponse:
    # Синтетическая API выключена до явного локального разрешения.
    import os
    if os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1":
        return _not_found(request)
    dsn = _enabled_dsn(request)
    if dsn is None:
        return _response(request, 403, "FORBIDDEN")
    return dsn



class PendingLinkPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customerId: UUID


@router.post("/api/v1/dev/auto/vehicles/{vehicle_id}/links")
async def propose_vehicle_link(vehicle_id: str, request: Request):
    """Создать неподтверждённую связь только по назначению INTAKE на авто."""
    from .auto_vehicle_link_intake import create_pending_vehicle_link, VehicleLinkConflict
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        ident = UUID(vehicle_id)
        data = PendingLinkPayload.model_validate(await request.json())
        key = validated_key(request.headers.get("idempotency-key"))
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            link_id = create_pending_vehicle_link(
                conn, request.cookies.get("rp_session", ""), ident, data.customerId,
                idempotency_key=key,
            )
        except (VehicleLinkConflict, AutoIdempotencyConflict):
            return _response(request, 409, "CONFLICT")
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(status_code=201,
                        content={"linkId": str(link_id), "status": "PENDING"},
                        headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/links/{link_id}/issue-proof")
def issue(link_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        link = UUID(link_id)
    except (ValueError, TypeError):
        return _not_found(request)
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            claim, proof = issue_auto_proof(conn, request.cookies.get("rp_session", ""), link)
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(
        content={"claimId": str(claim), "proof": proof},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/api/v1/dev/auto/claims/{claim_id}/redeem")
async def redeem(claim_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        ident = UUID(claim_id)
        data = ProofPayload.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = redeem_auto_proof(
                conn, request.cookies.get("rp_session", ""), ident, data.proof,
            )
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status": status}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/claims/{claim_id}/review")
async def review(claim_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        ident = UUID(claim_id)
        data = DecisionPayload.model_validate(await request.json())
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = review_auto_claim(
                conn, request.cookies.get("rp_session", ""), ident, data.approve,
            )
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status": status}, headers={"Cache-Control": "no-store"})


@router.post("/api/v1/dev/auto/links/{link_id}/revoke")
def revoke(link_id: str, request: Request):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        link = UUID(link_id)
    except (ValueError, TypeError):
        return _not_found(request)
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = revoke_auto_link(
                conn, request.cookies.get("rp_session", ""), link,
            )
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(content={"status": status}, headers={"Cache-Control": "no-store"})
