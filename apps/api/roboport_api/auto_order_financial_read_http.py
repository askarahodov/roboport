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



from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from .auth_http import _mutation_ok
from .auto_order_financial_post import post_work_order_financial
from .auto_stock_service import AutoStockConflict


class FinancialPosting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(pattern="^(CHARGE|CREDIT)$")
    amountMinor: StrictInt = Field(ge=1, le=1000000000000)
    reason: str = Field(min_length=3, max_length=160)


@router.post("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/financial-events")
async def post_financial_event(org_id: str, loc_id: str, order_id: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, order = UUID(org_id), UUID(loc_id), UUID(order_id)
        body = FinancialPosting.model_validate(await request.json())
        key = UUID(request.headers["Idempotency-Key"])
    except (KeyError, ValueError, ValidationError, TypeError, UnicodeDecodeError):
        return _response(request, 422, "INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request, db):
            return _response(request, 403, "FORBIDDEN")
        try:
            event_id = post_work_order_financial(
                db, request.cookies.get("rp_session", ""), org, loc, order,
                key, body.action, body.amountMinor, body.reason,
            )
        except AutoStockDenied as exc:
            return _response(request, 422 if str(exc) == "INVALID_INPUT" else 403, str(exc))
        except AutoStockConflict:
            return _response(request, 409, "CONFLICT")
    return JSONResponse(status_code=201, content={"eventId": str(event_id), "syntheticOnly": True},
                        headers={"Cache-Control": "no-store"})
