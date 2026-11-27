"""Closed synthetic read of customer and stock for the same work order."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_order_overview import get_order_overview
from .auto_stock_service import AutoStockDenied

router = APIRouter(include_in_schema=False)


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/overview")
def read_order_overview(org_id: str, loc_id: str, order_id: str,
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
            result = get_order_overview(
                db, request.cookies.get("rp_session", ""), org, loc, order,
                limit=limit, cursor=cursor,
            )
        except AutoStockDenied as exc:
            return _response(request, 422 if str(exc) == "INVALID_INPUT" else 403,
                             str(exc))
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders")
def staff_work_order_register(org_id: str,loc_id: str,request: Request,
                              stage: str="ALL",orderId: str|None=None,
                              cursor: str|None=None,view:str="legacy",
                              repairStage:str="ALL",documentStage:str="ALL",bookingStatus:str="ALL",
                              fromDay:str|None=None,toDay:str|None=None):
    """Live INTAKE/MANAGER document register: no customer PII or payments."""
    from .auto_work_order_register import (
        list_work_orders,RegisterDenied,RegisterInvalid,
    )
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if (any(len(request.query_params.getlist(name))>1
            for name in ("stage","orderId","cursor","view","repairStage","documentStage",
                         "bookingStatus","fromDay","toDay"))
        or (cursor is not None and (not cursor or len(cursor)>256))):
        return _response(request,422,"INVALID_INPUT")
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        lookup=UUID(orderId) if orderId is not None else None
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            data=list_work_orders(
                db,request.cookies.get("rp_session",""),org,loc,
                stage=stage,order_id=lookup,cursor=cursor,view=view,repair_stage=repairStage,
                document_stage=documentStage,booking_status=bookingStatus,from_day=fromDay,to_day=toDay,
            )
        except RegisterDenied:
            return _response(request,403,"FORBIDDEN")
        except RegisterInvalid:
            return _response(request,422,"INVALID_INPUT")
        except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
            return _response(request,409,"REGISTER_CONFLICT")
    return JSONResponse(content=data,headers={"Cache-Control":"no-store"})



@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/export")
def export_staff_work_order_register(org_id: str,loc_id: str,request: Request,
                                     stage: str="ALL",orderId: str|None=None,view:str="legacy",
                                     repairStage:str="ALL",documentStage:str="ALL",bookingStatus:str="ALL",
                                     fromDay:str|None=None,toDay:str|None=None):
    """Complete filtered register CSV; fail closed rather than truncate."""
    from .auto_work_order_register import (
        export_work_orders_csv,RegisterDenied,RegisterInvalid,
    )
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    allowed=("stage","orderId","view","repairStage","documentStage","bookingStatus","fromDay","toDay")
    if (any(len(request.query_params.getlist(name))>1 for name in allowed)
        or any(key not in allowed for key in request.query_params)):
        return _response(request,422,"INVALID_INPUT")
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        lookup=UUID(orderId) if orderId is not None else None
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=export_work_orders_csv(
                db,request.cookies.get("rp_session",""),org,loc,
                stage=stage,order_id=lookup,view=view,repair_stage=repairStage,
                document_stage=documentStage,booking_status=bookingStatus,from_day=fromDay,to_day=toDay,
            )
        except RegisterDenied:
            return _response(request,403,"FORBIDDEN")
        except RegisterInvalid as exc:
            if str(exc)=="EXPORT_LIMIT":
                return _response(request,409,"REGISTER_LIMIT")
            return _response(request,422,"INVALID_INPUT")
        except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
            return _response(request,409,"REGISTER_CONFLICT")
    return Response(
        content=result,media_type="text/csv; charset=utf-8",
        headers={"Cache-Control":"no-store",
                 "X-Content-Type-Options":"nosniff",
                 "Content-Disposition":'attachment; filename="auto-work-order-register.csv"'},
    )


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/workspace")
def staff_order_workspace(org_id: str,loc_id: str,order_id: str,request: Request):
    """Сводка этапов в одном филиале; специальные права разделов не обходятся."""
    from .auto_order_workspace import read_order_workspace
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc,order=UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=read_order_workspace(
                db,request.cookies.get("rp_session",""),org,loc,order,
            )
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
            return _response(request,409,"WORKSPACE_CONFLICT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store",
                                               "X-Content-Type-Options":"nosniff"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/timeline")
def staff_order_timeline(org_id: str,loc_id: str,order_id: str,
                         request: Request,limit: str="25",cursor: str|None=None):
    """Paged immutable technical events, without prices, payments or PII."""
    from .auto_order_timeline import read_order_timeline
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    if (len(request.query_params.getlist("limit"))>1
        or len(request.query_params.getlist("cursor"))>1
        or not limit.isascii() or not limit.isdecimal() or len(limit)>2
        or not 1<=int(limit)<=25
        or (cursor is not None and (not cursor or len(cursor)>256))):
        return _response(request,422,"INVALID_INPUT")
    try:
        org,loc,order=UUID(org_id),UUID(loc_id),UUID(order_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=read_order_timeline(
                db,request.cookies.get("rp_session",""),org,loc,order,
                limit=int(limit),cursor=cursor,
            )
        except AutoStockDenied as exc:
            return _response(request,422 if str(exc)=="INVALID_INPUT" else 403,
                             "INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN")
        except (psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
            return _response(request,409,"TIMELINE_CONFLICT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store",
                                                 "X-Content-Type-Options":"nosniff"})
