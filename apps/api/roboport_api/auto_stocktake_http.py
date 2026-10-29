"""Opt-in loopback-only warehouse stocktake HTTP; no production exposure."""
from uuid import UUID
import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictInt, Field, ValidationError, AwareDatetime
from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_stock_lot_stocktake import count_stock_lot, approve_stock_lot_count
from .auto_stock_receipt_document import create_receipt_document,accept_receipt_document
from .auto_stock_receipt_valuation import record_receipt_valuation
from .auto_stock_service import AutoStockDenied, AutoStockConflict

router = APIRouter(include_in_schema=False)


class CountBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    lotId: UUID
    actual: StrictInt = Field(ge=0, le=1000000000)
    reason: str = Field(min_length=3,max_length=240)


class ApproveBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    countId: UUID


def _key(request):
    header=request.headers.get("Idempotency-Key")
    if not header:
        raise ValueError("idempotency key required")
    return UUID(header)


@router.post("/api/v1/dev/auto/organizations/{org_id}/stocktake/counts")
async def create_count(org_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=CountBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=count_stock_lot(db,request.cookies.get("rp_session",""),
                                  org,data.locationId,data.lotId,key,data.actual,data.reason)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"countId":str(ident)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/stocktake/approvals")
async def approve_count(org_id: str, request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ApproveBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=approve_stock_lot_count(db,request.cookies.get("rp_session",""),
                                          org,data.locationId,data.countId,key)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"approvalId":str(ident)},
                        headers={"Cache-Control":"no-store"})


@router.get("/api/v1/dev/auto/organizations/{org_id}/stocktake/lots")
def get_lots(org_id: str, request: Request, locationId: str,
             limit: int = 30, afterLotId: str | None = None):
    from .auto_stock_lot_read import list_stocktake_lots
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        organization=UUID(org_id)
        location=UUID(locationId)
        cursor=UUID(afterLotId) if afterLotId is not None else None
        if not 1<=limit<=50:
            raise ValueError("invalid page size")
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            items,next_id=list_stocktake_lots(
                db,request.cookies.get("rp_session",""),organization,location,
                limit,cursor)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
    return JSONResponse(content={
        "items":items,"nextLotId":str(next_id) if next_id else None,
    },headers={"Cache-Control":"no-store"})


class ReceiptDraftBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    lotId: UUID
    quantity: StrictInt = Field(ge=1,le=1000000000)
    sourceReference: str = Field(min_length=3,max_length=160)


class ReceiptAcceptBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    documentId: UUID


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/receipts")
async def create_receipt(org_id: str,request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ReceiptDraftBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            document_id=create_receipt_document(
                db,request.cookies.get("rp_session",""),org,
                data.locationId,data.lotId,key,data.quantity,data.sourceReference)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"documentId":str(document_id)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/receipts/accept")
async def accept_receipt(org_id: str,request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ReceiptAcceptBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            acceptance_id=accept_receipt_document(
                db,request.cookies.get("rp_session",""),org,data.locationId,
                data.documentId,key)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"acceptanceId":str(acceptance_id)},
                        headers={"Cache-Control":"no-store"})


class ReceiptValuationBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    documentId: UUID
    currency: str = Field(pattern="^[A-Z]{3}$")
    unitNetMinor: StrictInt = Field(ge=0,le=1000000000)
    vatTotalMinor: StrictInt = Field(ge=0,le=1000000000000000000)
    supplierPriceRevision: str = Field(min_length=3,max_length=160)
    supplierQuotedAt: AwareDatetime


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/receipts/valuation")
async def value_receipt(org_id: str,request: Request):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ReceiptValuationBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=record_receipt_valuation(
                db,request.cookies.get("rp_session",""),org,
                data.locationId,data.documentId,key,
                data.currency,data.unitNetMinor,data.vatTotalMinor,
                data.supplierPriceRevision,data.supplierQuotedAt)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"valuationId":str(ident)},
                        headers={"Cache-Control":"no-store"})
