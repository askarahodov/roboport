"""Private synthetic HTTPS customer history: list and one WorkOrder.

The order id is an opaque locator, never an authorization credential.
"""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse, Response
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_vehicle_http import _not_found,_page_int
from .auto_customer_orders import (
    CustomerOrderDenied,CustomerOrderInvalid,
    list_customer_orders,customer_order_details,export_customer_orders,CustomerOrderLimit,
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
    if isinstance(exc,CustomerOrderLimit):
        return _response(request,409,"EXPORT_LIMIT")
    return _response(request,422,"INVALID_INPUT")


def _vehicle_selector(value):
    if value is None:
        return None
    try:
        ident=UUID(value)
        if len(value)!=36 or str(ident)!=value.lower():
            raise ValueError("noncanonical uuid")
        return ident
    except (ValueError,TypeError,AttributeError):
        raise CustomerOrderInvalid("INVALID_INPUT") from None


@router.get("/api/v1/dev/auto/customer/orders")
def list_orders(request:Request,limit:str="20",cursor:str|None=None,
                search:str="",status:str="ALL",fromDay:str|None=None,toDay:str|None=None,
                vehicleId:str|None=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    page_limit=_page_int(limit,minimum=1,maximum=30)
    if (page_limit is None or any(len(request.query_params.getlist(key))>1
        for key in ("limit","cursor","search","status","fromDay","toDay","vehicleId"))
        or (cursor is not None and (not cursor or len(cursor)>256))):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=list_customer_orders(
                db,request.cookies.get("rp_session",""),limit=page_limit,cursor=cursor,
                search=search,status=status,from_day=fromDay,to_day=toDay,
                vehicle_id=_vehicle_selector(vehicleId))
        except (CustomerOrderDenied,CustomerOrderInvalid) as exc:
            return _err(request,exc)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/customer/orders/export")
def export_orders(request:Request,search:str="",status:str="ALL",
                  fromDay:str|None=None,toDay:str|None=None,vehicleId:str|None=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if (set(request.query_params)-{"search","status","fromDay","toDay","vehicleId"}
        or any(len(request.query_params.getlist(key))>1 for key in
               ("search","status","fromDay","toDay","vehicleId"))):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            data=export_customer_orders(db,request.cookies.get("rp_session",""),
                search=search,status=status,from_day=fromDay,to_day=toDay,
                vehicle_id=_vehicle_selector(vehicleId))
        except (CustomerOrderDenied,CustomerOrderInvalid,CustomerOrderLimit) as exc:
            return _err(request,exc)
    return Response(content=data,media_type="text/csv; charset=utf-8",
        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                 "Content-Disposition":'attachment; filename="auto-customer-orders-dev.csv"'})


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
