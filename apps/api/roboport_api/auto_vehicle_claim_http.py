"""Синтетический клиентско-сотруднический процесс подтверждения автомобиля.

Публичный API и обработка реальных доказательств запрещены до release gate.
"""
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError, Field, StrictInt

from .auth_http import _mutation_ok
from .auto_create_idempotency import validated_key, AutoIdempotencyConflict
from .auto_claim_retry import AutoClaimRetryConflict
from .auto_proof_throttle import AutoProofRateLimited
from .auto_vehicle_http import _enabled_dsn, _not_found
from .auto_vehicle_claim import (
    VehicleClaimRejected, issue_auto_proof, reissue_auto_proof, redeem_auto_proof,
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
        except AutoProofRateLimited:
            return JSONResponse(
                status_code=429,
                content={"code": "RATE_LIMITED", "requestId": request.state.request_id},
                headers={"Cache-Control": "no-store", "Retry-After": "3600"},
            )
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(
        content={"claimId": str(claim), "proof": proof},
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post("/api/v1/dev/auto/links/{link_id}/reissue-proof")
def reissue(link_id: str, request: Request):
    """Только исходный INTAKE может однократно заменить утерянный секрет.

    Link ID известен сотруднику до ISSUE, поэтому API работает даже если
    HTTP ответ ISSUE с claim ID был потерян. Старый proof не возвращается.
    """
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
            claim, proof = reissue_auto_proof(
                conn, request.cookies.get("rp_session", ""), link,
            )
        except AutoProofRateLimited:
            return JSONResponse(
                status_code=429,
                content={"code": "RATE_LIMITED", "requestId": request.state.request_id},
                headers={"Cache-Control": "no-store", "Retry-After": "3600"},
            )
        except VehicleClaimRejected:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(
        content={"claimId": str(claim), "proof": proof, "status": "REISSUED"},
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
        key = validated_key(request.headers.get("idempotency-key"))
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = redeem_auto_proof(
                conn, request.cookies.get("rp_session", ""), ident, data.proof,
                idempotency_key=key,
            )
        except AutoProofRateLimited:
            return JSONResponse(
                status_code=429,
                content={"code": "RATE_LIMITED", "requestId": request.state.request_id},
                headers={"Cache-Control": "no-store", "Retry-After": "900"},
            )
        except AutoClaimRetryConflict:
            return _response(request, 409, "CONFLICT")
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
        key = validated_key(request.headers.get("idempotency-key"))
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            status = review_auto_claim(
                conn, request.cookies.get("rp_session", ""), ident, data.approve,
                idempotency_key=key,
            )
        except AutoClaimRetryConflict:
            return _response(request, 409, "CONFLICT")
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


# Закрытая регистрация: новый VIN или отсутствие VIN, никогда поиск по чужому VIN.
class RegistrationFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locationId: UUID
    make: str = Field(min_length=1,max_length=100)
    model: str = Field(min_length=1,max_length=120)
    variant: str | None = Field(default=None,min_length=1,max_length=160)
    modelYear: StrictInt | None = Field(default=None,ge=1886,le=2100)


class RegistrationCreate(RegistrationFields):
    customerId: UUID
    vin: str | None = Field(default=None,pattern=r"^[A-HJ-NPR-Z0-9]{17}$")


class RegistrationEdit(RegistrationFields):
    expectedRevision: StrictInt = Field(ge=1,lt=1000000)


async def _registration_mutation(org_id,request,vehicle_id=None):
    from .auto_vehicle_registration import register_vehicle,edit_vehicle,RegistrationDenied,RegistrationConflict
    config=_request_dsn(request)
    if isinstance(config,JSONResponse):
        return config
    try:
        org=UUID(org_id)
        vehicle=UUID(vehicle_id) if vehicle_id is not None else None
        data=(RegistrationCreate if vehicle is None else RegistrationEdit).model_validate(await request.json())
        key=UUID(request.headers["Idempotency-Key"])
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError,KeyError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(config) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        token=request.cookies.get("rp_session","")
        fields=dict(make=data.make,model=data.model,variant=data.variant,year=data.modelYear)
        try:
            if vehicle is None:
                result=register_vehicle(db,token,org,data.locationId,data.customerId,key,vin=data.vin,**fields)
            else:
                result=edit_vehicle(db,token,org,data.locationId,vehicle,key,
                                    expected_revision=data.expectedRevision,**fields)
        except ValueError:
            return _response(request,422,"INVALID_INPUT")
        except RegistrationDenied:
            return _response(request,403,"FORBIDDEN")
        except RegistrationConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201 if vehicle is None else 200,content=result,
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/vehicle-registrations")
async def create_vehicle_registration(org_id:str,request:Request):
    return await _registration_mutation(org_id,request)


@router.post("/api/v1/dev/auto/organizations/{org_id}/vehicle-registrations/{vehicle_id}/revisions")
async def revise_vehicle_registration(org_id:str,vehicle_id:str,request:Request):
    return await _registration_mutation(org_id,request,vehicle_id)


@router.get("/api/v1/dev/auto/organizations/{org_id}/vehicle-registrations/{vehicle_id}")
def read_vehicle_registration(org_id:str,vehicle_id:str,request:Request):
    from .auto_vehicle_registration import registration_details,RegistrationDenied
    config=_request_dsn(request)
    if isinstance(config,JSONResponse):
        return config
    try:
        if set(request.query_params.keys())!={"locationId"} or len(request.query_params.getlist("locationId"))!=1:
            raise ValueError("invalid scope")
        org,vehicle,loc=UUID(org_id),UUID(vehicle_id),UUID(request.query_params["locationId"])
    except (ValueError,TypeError,KeyError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(config) as db:
        try:
            result=registration_details(db,request.cookies.get("rp_session",""),org,loc,vehicle)
        except RegistrationDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
