"""Только синтетические HTTPS/loopback чтения авто до согласования публичного API."""
import os
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auth_http import _dsn, _error
from .auto_vehicle_read import owned_vehicle, owned_vehicles

router = APIRouter(include_in_schema=False)


def _not_found(request: Request) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content={
            "code": "NOT_FOUND",
            "message": "Ресурс не найден",
            "requestId": request.state.request_id,
        },
        headers={"Cache-Control": "no-store"},
    )


def _enabled_dsn(request: Request) -> str | None:
    if os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1":
        return None
    return _dsn(request)


@router.get("/api/v1/dev/auto/vehicles")
def list_vehicles(request: Request, limit: int = 20, offset: int = 0):
    if os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1":
        return _not_found(request)
    dsn = _enabled_dsn(request)
    if dsn is None:
        return _error(request, 403)
    if not 1 <= limit <= 50 or not 0 <= offset <= 10000:
        return JSONResponse(
            status_code=422,
            content={
                "code": "INVALID_INPUT", "message": "Неверная пагинация",
                "requestId": request.state.request_id,
            }, headers={"Cache-Control": "no-store"},
        )
    with psycopg.connect(dsn) as conn:
        items = owned_vehicles(
            conn, request.cookies.get("rp_session", ""),
            limit=limit, offset=offset,
        )
    if items is None:
        return _error(request, 401)
    return JSONResponse(content={"items": items}, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/vehicles/{vehicle_id}")
def vehicle_details(vehicle_id: str, request: Request):
    if os.getenv("ROBOport_SYNTHETIC_AUTO_API") != "1":
        return _not_found(request)
    dsn = _enabled_dsn(request)
    if dsn is None:
        return _error(request, 403)
    try:
        uid = UUID(vehicle_id)
    except (ValueError, TypeError):
        return _not_found(request)
    with psycopg.connect(dsn) as conn:
        authenticated, result = owned_vehicle(
            conn, request.cookies.get("rp_session", ""), uid,
        )
    if not authenticated:
        return _error(request, 401)
    if result is None:
        return _not_found(request)
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
