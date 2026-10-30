"""Closed synthetic read of customer and stock for the same work order."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_order_overview import get_order_overview
from .auto_stock_service import AutoStockDenied

router = APIRouter(include_in_schema=False)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/overview")
def read_order_overview(org_id: str, loc_id: str, order_id: str,
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
            result = get_order_overview(
                db, request.cookies.get("rp_session", ""), org, loc, order,
                limit=limit,
            )
        except AutoStockDenied as exc:
            return _response(request, 422 if str(exc) == "INVALID_INPUT" else 403,
                             str(exc))
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})
