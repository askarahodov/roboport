"""Closed synthetic manager-only service selection for automobile WorkOrders."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_order_service_link import (
    add_synthetic_order_service, list_synthetic_order_services,
    list_synthetic_service_catalog, OrderServiceDenied,
)

router = APIRouter(include_in_schema=False)


class ServiceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    serviceId: UUID


def _scope(request: Request, org_id: str, loc_id: str,
           order_id: str | None = None):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        identifiers = [UUID(org_id), UUID(loc_id)]
        if order_id is not None:
            identifiers.append(UUID(order_id))
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    return dsn, identifiers


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/service-catalog")
def catalog(org_id: str, loc_id: str, request: Request):
    scoped = _scope(request, org_id, loc_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, (org, loc) = scoped
    with psycopg.connect(dsn) as conn:
        try:
            items = list_synthetic_service_catalog(
                conn, request.cookies.get("rp_session", ""),
                organization_id=org, location_id=loc,
            )
        except OrderServiceDenied as error:
            return _response(request, 503 if str(error) == "LIMIT" else 403,
                             "UNAVAILABLE" if str(error) == "LIMIT" else "FORBIDDEN")
    return JSONResponse(content={"items": items}, headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/services")
def selected(org_id: str, loc_id: str, order_id: str, request: Request):
    scoped = _scope(request, org_id, loc_id, order_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, (org, loc, order) = scoped
    with psycopg.connect(dsn) as conn:
        try:
            items = list_synthetic_order_services(
                conn, request.cookies.get("rp_session", ""),
                organization_id=org, location_id=loc, work_order_id=order,
            )
        except OrderServiceDenied as error:
            return _response(request, 503 if str(error) == "LIMIT" else 403,
                             "UNAVAILABLE" if str(error) == "LIMIT" else "FORBIDDEN")
    return JSONResponse(content={"items": items}, headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/services")
async def add_service(org_id: str, loc_id: str, order_id: str, request: Request):
    scoped = _scope(request, org_id, loc_id, order_id)
    if isinstance(scoped, JSONResponse):
        return scoped
    dsn, (org, loc, order) = scoped
    try:
        body = ServiceSelection.model_validate(await request.json())
    except (ValueError, TypeError, ValidationError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as conn:
        if not _mutation_ok(request, conn):
            return _response(request, 403, "FORBIDDEN")
        try:
            result = add_synthetic_order_service(
                conn, request.cookies.get("rp_session", ""),
                organization_id=org, location_id=loc, work_order_id=order,
                service_id=body.serviceId, synthetic_only=True,
            )
        except OrderServiceDenied:
            return _response(request, 403, "FORBIDDEN")
    return JSONResponse(status_code=200 if result["replayed"] else 201,
                        content=result, headers={"Cache-Control":"no-store"})
