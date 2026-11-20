"""Закрытые dev routes создания и редактирования филиального автомобиля."""

from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auto_create_idempotency import AutoIdempotencyConflict, validated_key
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_vehicle_registry import (
    VehicleRegistryConflict, VehicleRegistryDenied,
    create_vehicle_with_pending_link, update_vehicle,
)
from .auto_vehicle_link_intake import VehicleLinkConflict
from .auth_http import _mutation_ok


router = APIRouter(include_in_schema=False)


class CreateVehiclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customerId: UUID
    vin: str | None = Field(default=None, max_length=17)
    make: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=120)
    variant: str | None = Field(default=None, max_length=160)
    modelYear: int | None = Field(default=None, ge=1886, le=2100)


class UpdateVehiclePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vin: str | None = Field(default=None, max_length=17)
    make: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=120)
    variant: str | None = Field(default=None, max_length=160)
    modelYear: int | None = Field(default=None, ge=1886, le=2100)


@router.post(
    "/api/v1/dev/auto/organizations/{organization_id}/locations/{location_id}/vehicles"
)
async def create_vehicle(
    organization_id: str, location_id: str, request: Request,
):
    """INTAKE создаёт автомобиль, branch binding и pending customer link атомарно."""
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        organization = UUID(organization_id)
        location = UUID(location_id)
        data = CreateVehiclePayload.model_validate(await request.json())
        key = validated_key(request.headers.get("idempotency-key"))
        if key is None:
            raise ValueError("idempotency key required")
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")

    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = create_vehicle_with_pending_link(
                conn, request.cookies.get("rp_session", ""),
                organization, location, data.customerId,
                {
                    "vin": data.vin, "make": data.make, "model": data.model,
                    "variant": data.variant, "modelYear": data.modelYear,
                },
                key,
            )
        except VehicleRegistryDenied:
            return _response(request, 403, "FORBIDDEN")
        except (VehicleRegistryConflict, VehicleLinkConflict, AutoIdempotencyConflict):
            return _response(request, 409, "CONFLICT")
        except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation,
                psycopg.errors.RaiseException):
            return _response(request, 409, "CONFLICT")
        except ValueError:
            return _response(request, 422, "INVALID_INPUT")

    return JSONResponse(
        status_code=200 if result["replayed"] else 201,
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/api/v1/dev/auto/organizations/{organization_id}/locations/{location_id}"
    "/vehicles/{vehicle_id}/edit"
)
async def edit_vehicle(
    organization_id: str, location_id: str, vehicle_id: str, request: Request,
):
    """MANAGER редактирует только автомобиль, закреплённый за своим филиалом."""
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        organization = UUID(organization_id)
        location = UUID(location_id)
        vehicle = UUID(vehicle_id)
        data = UpdateVehiclePayload.model_validate(await request.json())
        key = validated_key(request.headers.get("idempotency-key"))
        if key is None:
            raise ValueError("idempotency key required")
        fields = data.model_dump(exclude_unset=True)
    except (ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")

    with psycopg.connect(config) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = update_vehicle(
                conn, request.cookies.get("rp_session", ""),
                organization, location, vehicle, fields, key,
            )
        except VehicleRegistryDenied:
            return _response(request, 403, "FORBIDDEN")
        except (VehicleRegistryConflict, AutoIdempotencyConflict):
            return _response(request, 409, "CONFLICT")
        except (psycopg.errors.UniqueViolation, psycopg.errors.CheckViolation,
                psycopg.errors.RaiseException):
            return _response(request, 409, "CONFLICT")
        except ValueError:
            return _response(request, 422, "INVALID_INPUT")

    return JSONResponse(
        content=result,
        headers={"Cache-Control": "no-store"},
    )
