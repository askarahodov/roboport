"""Private synthetic HTTPS customer history: list and one WorkOrder.

The order id is an opaque locator, never an authorization credential.
"""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_vehicle_http import _not_found,_page_int
from .auto_customer_orders import (
    CustomerOrderDenied,CustomerOrderInvalid,
    list_customer_orders,customer_order_details,
)

router=APIRouter(include_in_schema=False)


def _err(request,exc):
    code=str(exc)
    if code=="AUTH_REQUIRED":
        return _response(request,401,"AUTH_REQUIRED")
    if code=="FORBIDDEN":
        return _response(request,403,"FORBIDDEN")
    if code=="NOT_FOUND":
        return _not_found(request)
    return _response(request,422,"INVALID_INPUT")


@router.get("/api/v1/dev/auto/customer/orders")
def list_orders(request:Request,limit:str="20",cursor:str|None=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    page_limit=_page_int(limit,minimum=1,maximum=30)
    if (page_limit is None or len(request.query_params.getlist("limit"))>1
        or len(request.query_params.getlist("cursor"))>1
        or (cursor is not None and (not cursor or len(cursor)>256))):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=list_customer_orders(
                db,request.cookies.get("rp_session",""),limit=page_limit,cursor=cursor)
        except (CustomerOrderDenied,CustomerOrderInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/customer/orders/{order_id}")
def read_order(order_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        ident=UUID(order_id)
    except (ValueError,TypeError):
        return _not_found(request)
    with psycopg.connect(dsn) as db:
        try:
            result=customer_order_details(
                db,request.cookies.get("rp_session",""),ident)
        except (CustomerOrderDenied,CustomerOrderInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
