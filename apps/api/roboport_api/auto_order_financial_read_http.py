"""Closed loopback-only synthetic WorkOrder financial balance endpoint."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_order_financial_read import get_work_order_balance, list_work_order_financial_events
from .auto_stock_service import AutoStockDenied

router = APIRouter(include_in_schema=False)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/financial-balance")
def read_work_order_financial_balance(org_id: str, loc_id: str, order_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, order = UUID(org_id), UUID(loc_id), UUID(order_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result = get_work_order_balance(
                db, request.cookies.get("rp_session", ""), org, loc, order,
            )
        except AutoStockDenied as exc:
            return _response(request, 422 if str(exc) == "INVALID_INPUT" else 403, str(exc))
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/financial-history")
def read_work_order_financial_history(org_id: str, loc_id: str, order_id: str,
                                      request: Request, limit: int = 50, cursor: str | None = None):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, order = UUID(org_id), UUID(loc_id), UUID(order_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result = list_work_order_financial_events(
                db, request.cookies.get("rp_session", ""), org, loc, order, limit=limit, cursor=cursor,
            )
        except AutoStockDenied as exc:
            return _response(request, 422 if str(exc) == "INVALID_INPUT" else 403, str(exc))
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
