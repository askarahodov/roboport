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


class WorkOrderLotBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    workOrderId: UUID
    lotId: UUID
    quantity: StrictInt = Field(ge=1,le=1000000000)


async def _mutate_work_order_lot(org_id: str,request: Request,action: str):
    from .auto_stock_lot_work_order_issue import issue_work_order_from_lot
    from .auto_stock_lot_work_order_return import return_work_order_to_lot
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=WorkOrderLotBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            handler=issue_work_order_from_lot if action=="issue" else return_work_order_to_lot
            ident=handler(db,request.cookies.get("rp_session",""),org,
                data.locationId,data.workOrderId,data.lotId,key,data.quantity)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,
        content={("issueId" if action=="issue" else "returnId"):str(ident)},
        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/work-orders/issue")
async def issue_work_order_lot_http(org_id: str,request: Request):
    return await _mutate_work_order_lot(org_id,request,"issue")


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/work-orders/return")
async def return_work_order_lot_http(org_id: str,request: Request):
    return await _mutate_work_order_lot(org_id,request,"return")


class MechanicHandoffBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    issueId: UUID


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/work-orders/acknowledge")
async def acknowledge_work_order_lot_http(org_id: str,request: Request):
    from .auto_stock_mechanic_handoff import acknowledge_mechanic_handoff
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=MechanicHandoffBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=acknowledge_mechanic_handoff(
                db,request.cookies.get("rp_session",""),org,data.locationId,
                data.issueId,key)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"handoffId":str(result)},
                        headers={"Cache-Control":"no-store"})


class SerialRegisterBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    lotId: UUID
    serialNumber: str = Field(min_length=3,max_length=120)


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/serials")
async def register_serial_http(org_id: str,request: Request):
    from .auto_stock_serial import register_lot_serial
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=SerialRegisterBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=register_lot_serial(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.lotId,key,data.serialNumber)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"serialId":str(result)},
                        headers={"Cache-Control":"no-store"})


class ReclassificationDraftBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    sourceLotId: UUID
    destinationLotId: UUID
    quantity: StrictInt = Field(ge=1,le=1000000000)
    reason: str = Field(min_length=3,max_length=240)


class ReclassificationApprovalBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    documentId: UUID


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/reclassifications")
async def create_reclassification_http(org_id: str, request: Request):
    from .auto_stock_reclassification import propose_reclassification
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ReclassificationDraftBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=propose_reclassification(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.sourceLotId,data.destinationLotId,
                key,data.quantity,data.reason)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"documentId":str(ident)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/reclassifications/approvals")
async def approve_reclassification_http(org_id: str, request: Request):
    from .auto_stock_reclassification import approve_reclassification
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=ReclassificationApprovalBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=approve_reclassification(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.documentId,key)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"approvalId":str(ident)},
                        headers={"Cache-Control":"no-store"})


class SerialMovementBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    serialId: UUID
    workOrderId: UUID | None = None
    destinationLotId: UUID | None = None


async def _serial_movement_http(org_id: str,request: Request,action: str):
    from .auto_stock_serial_movement import move_lot_serial
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        data=SerialMovementBody.model_validate(await request.json())
        key=_key(request)
        if (action in ("ISSUE","RETURN") and (
            data.workOrderId is None or data.destinationLotId is not None)
            or action=="TRANSFER" and (
            data.destinationLotId is None or data.workOrderId is not None)):
            raise ValueError("invalid serial transition")
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=move_lot_serial(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.serialId,key,action,
                data.workOrderId,data.destinationLotId)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"movementId":str(ident)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/serial-movements/issue")
async def serial_issue_http(org_id: str,request: Request):
    return await _serial_movement_http(org_id,request,"ISSUE")


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/serial-movements/return")
async def serial_return_http(org_id: str,request: Request):
    return await _serial_movement_http(org_id,request,"RETURN")


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/serial-movements/transfer")
async def serial_transfer_http(org_id: str,request: Request):
    return await _serial_movement_http(org_id,request,"TRANSFER")


class SupplierReturnDraftBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    sourceReceiptId: UUID
    quantity: StrictInt = Field(ge=1,le=1000000000)
    reason: str = Field(min_length=3,max_length=240)
    supplierReference: str = Field(min_length=3,max_length=160)


class SupplierReturnAcceptBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId: UUID
    documentId: UUID


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-returns")
async def draft_supplier_return_http(org_id:str,request:Request):
    from .auto_stock_supplier_return_document import draft_supplier_return
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse): return dsn
    try:
        org=UUID(org_id)
        data=SupplierReturnDraftBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=draft_supplier_return(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.sourceReceiptId,key,data.quantity,
                data.reason,data.supplierReference)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"documentId":str(ident)},
                        headers={"Cache-Control":"no-store"})


@router.post("/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-returns/approvals")
async def approve_supplier_return_http(org_id:str,request:Request):
    from .auto_stock_supplier_return_document import approve_supplier_return
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse): return dsn
    try:
        org=UUID(org_id)
        data=SupplierReturnAcceptBody.model_validate(await request.json())
        key=_key(request)
    except (ValueError,ValidationError,TypeError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            ident=approve_supplier_return(db,request.cookies.get("rp_session",""),
                org,data.locationId,data.documentId,key)
        except AutoStockDenied:
            return _response(request,403,"FORBIDDEN")
        except AutoStockConflict:
            return _response(request,409,"CONFLICT")
    return JSONResponse(status_code=201,content={"approvalId":str(ident)},
                        headers={"Cache-Control":"no-store"})
