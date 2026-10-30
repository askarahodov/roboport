"""Closed synthetic tenant warehouse view; never public production data."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_order_stock_read import list_work_order_stock
from .auto_stock_service import AutoStockDenied

router = APIRouter(include_in_schema=False)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/stock")
def get_order_stock(org_id: str, loc_id: str, order_id: str,
                    request: Request, limit: int = 50):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, order = UUID(org_id), UUID(loc_id), UUID(order_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            items = list_work_order_stock(
                db, request.cookies.get("rp_session", ""), org, loc, order, limit=limit
            )
        except AutoStockDenied as exc:
            status = 422 if str(exc) == "INVALID_INPUT" else 403
            return _response(request, status, str(exc))
    return JSONResponse(content={"items":items},headers={"Cache-Control":"no-store"})
