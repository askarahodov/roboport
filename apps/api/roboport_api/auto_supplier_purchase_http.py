"""Same-origin synthetic supplier order HTTP. Never mounted outside the dev gate."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field,StrictInt,ValidationError
from .auto_vehicle_claim_http import _request_dsn,_response
from .auth_http import _mutation_ok
from .auto_stock_service import AutoStockDenied,AutoStockConflict
from .auto_supplier_purchase import (
    place_supplier_order,record_supplier_delivery,cancel_supplier_remainder,
    list_supplier_orders,
)

router=APIRouter(include_in_schema=False)


class OrderBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    workOrderId:UUID
    lotId:UUID
    desiredQuantity:StrictInt=Field(ge=1,le=1_000_000_000)
    offers:list[dict]=Field(min_length=2,max_length=5)
    selectedSupplierId:str=Field(min_length=1,max_length=160)
    selectedOfferId:str=Field(min_length=1,max_length=160)


class DeliveryBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    quantity:StrictInt=Field(ge=1,le=1_000_000_000)
    supplierReference:str=Field(min_length=3,max_length=160)


class CancelBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID


def _key(request:Request)->UUID:
    return UUID(request.headers["Idempotency-Key"])


def _error(request:Request,exc:Exception):
    if isinstance(exc,AutoStockDenied):
        return _response(request,422 if str(exc)=="INVALID_INPUT" else 403,
                         "INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN")
    return _response(request,409,"CONFLICT")


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-orders")
async def create_supplier_order(org_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):return dsn
    try:
        org=UUID(org_id); key=_key(request)
        data=OrderBody.model_validate(await request.json())
    except (ValueError,TypeError,KeyError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):return _response(request,403,"FORBIDDEN")
        try:
            ident,qty=place_supplier_order(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.workOrderId,data.lotId,key,
                data.desiredQuantity,data.offers,data.selectedSupplierId,data.selectedOfferId)
        except (AutoStockDenied,AutoStockConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=201,content={"orderId":str(ident),
        "orderedQuantity":qty,"syntheticOnly":True},headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-orders")
def get_supplier_orders(org_id:str,request:Request,locationId:str,
                        limit:int=20,afterOrderId:str|None=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):return dsn
    try:
        org=UUID(org_id);loc=UUID(locationId)
        after=UUID(afterOrderId) if afterOrderId is not None else None
        if not 1<=limit<=50:raise ValueError("limit")
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            items,nxt=list_supplier_orders(db,request.cookies.get("rp_session",""),
                                           org,loc,limit,after)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content={"syntheticOnly":True,"items":items,
        "nextOrderId":str(nxt) if nxt else None},headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-orders/{order_id}/receipts")
async def add_supplier_delivery(org_id:str,order_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):return dsn
    try:
        org=UUID(org_id);order=UUID(order_id);key=_key(request)
        data=DeliveryBody.model_validate(await request.json())
    except (ValueError,TypeError,KeyError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):return _response(request,403,"FORBIDDEN")
        try:
            ident,doc=record_supplier_delivery(db,request.cookies.get("rp_session",""),
                org,data.locationId,order,key,data.quantity,data.supplierReference)
        except (AutoStockDenied,AutoStockConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=201,content={"deliveryId":str(ident),
        "documentId":str(doc),"syntheticOnly":True},headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-orders/{order_id}/cancel")
async def cancel_supplier_order(org_id:str,order_id:str,request:Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):return dsn
    try:
        org=UUID(org_id);order=UUID(order_id);key=_key(request)
        data=CancelBody.model_validate(await request.json())
    except (ValueError,TypeError,KeyError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):return _response(request,403,"FORBIDDEN")
        try:
            ident,qty=cancel_supplier_remainder(db,request.cookies.get("rp_session",""),
                                               org,data.locationId,order,key)
        except (AutoStockDenied,AutoStockConflict) as exc:
            return _error(request,exc)
    return JSONResponse(status_code=201,content={"cancelId":str(ident),
        "canceledQuantity":qty,"syntheticOnly":True},headers={"Cache-Control":"no-store"})
