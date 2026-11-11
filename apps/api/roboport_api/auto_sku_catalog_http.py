"""Closed HTTPS-only synthetic automotive nomenclature HTTP (no public OpenAPI)."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse,Response
from pydantic import BaseModel,ConfigDict,StrictInt,Field,ValidationError

from .auto_vehicle_claim_http import _request_dsn,_response
from .auth_http import _mutation_ok
from .auto_sku_catalog import (
    CatalogDenied,CatalogConflict,CatalogInvalid,
    list_catalog,create_catalog_sku,bulk_prices,link_analog,logical_merge,
    catalog_preview,catalog_commit,catalog_xlsx_export,
)
from .auto_catalog_xlsx import MAX_BYTES

router=APIRouter(include_in_schema=False)
ROOT="/api/v1/dev/auto/organizations/{org_id}/warehouse/catalog"


class SkuBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    article:str=Field(min_length=2,max_length=80)
    category:str=Field(min_length=2,max_length=32)
    uom:str
    barcode:str|None=None
    brand:str|None=None
    purchaseMinor:StrictInt=Field(ge=0,le=1_000_000_000)
    saleMinor:StrictInt=Field(ge=0,le=1_000_000_000)


class PriceLine(BaseModel):
    model_config=ConfigDict(extra="forbid")
    skuId:UUID
    expectedVersion:StrictInt=Field(ge=1,le=1_000_000_000)
    purchaseMinor:StrictInt=Field(ge=0,le=1_000_000_000)
    saleMinor:StrictInt=Field(ge=0,le=1_000_000_000)


class PricesBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    entries:list[PriceLine]=Field(min_length=1,max_length=100)


class AnalogBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    leftSkuId:UUID
    rightSkuId:UUID
    evidenceNote:str=Field(min_length=3,max_length=160)


class MergeBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    sourceSkuId:UUID
    targetSkuId:UUID


class ImportBody(BaseModel):
    model_config=ConfigDict(extra="forbid")
    locationId:UUID
    previewId:UUID
    sha256:str=Field(min_length=64,max_length=64)


def _key(request):
    return UUID(request.headers["Idempotency-Key"])


def _error(request,exc):
    if isinstance(exc,CatalogInvalid):
        return _response(request,422,"INVALID_INPUT")
    if isinstance(exc,CatalogDenied):
        return _response(request,403,"FORBIDDEN")
    return _response(request,409,"CONFLICT")


def _pg_conflict():
    return (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
            psycopg.errors.ForeignKeyViolation,psycopg.errors.CheckViolation,
            psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected,
            psycopg.errors.LockNotAvailable)


def _json(result,status=200):
    return JSONResponse(status_code=status,content=result,
                        headers={"Cache-Control":"no-store"})


def _prepare(request,org_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    return dsn,org


async def _body(request,model):
    return model.model_validate(await request.json())


async def _post(request,org_id,model,operation):
    prepared=_prepare(request,org_id)
    if isinstance(prepared,JSONResponse):
        return prepared
    dsn,org=prepared
    try:
        key=_key(request)
        data=await _body(request,model)
    except (ValueError,TypeError,KeyError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        try:
            result=operation(db,request.cookies.get("rp_session",""),org,key,data)
        except (CatalogInvalid,CatalogDenied,CatalogConflict) as exc:
            return _error(request,exc)
        except _pg_conflict():
            return _response(request,409,"CONFLICT")
    return _json(result,201)


@router.get(ROOT)
def get_catalog(org_id:str,request:Request,locationId:str,
                limit:int=30,afterSkuId:str|None=None):
    prepared=_prepare(request,org_id)
    if isinstance(prepared,JSONResponse):
        return prepared
    dsn,org=prepared
    try:
        loc=UUID(locationId)
        after=UUID(afterSkuId) if afterSkuId is not None else None
        if not 1<=limit<=50:
            raise ValueError("limit")
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            result=list_catalog(db,request.cookies.get("rp_session",""),
                                org,loc,limit,after)
        except (CatalogInvalid,CatalogDenied,CatalogConflict) as exc:
            return _error(request,exc)
    return _json(result)


@router.post(ROOT+"/skus")
async def post_sku(org_id:str,request:Request):
    def apply(db,token,org,key,data):
        return create_catalog_sku(db,token,org,data.locationId,key,{
            **data.model_dump(exclude={"locationId"}),"analogArticle":None})
    return await _post(request,org_id,SkuBody,apply)


@router.post(ROOT+"/prices")
async def post_prices(org_id:str,request:Request):
    return await _post(request,org_id,PricesBody,
        lambda db,token,org,key,data:bulk_prices(db,token,org,data.locationId,
            key,[entry.model_dump() for entry in data.entries]))


@router.post(ROOT+"/analogs")
async def post_analog(org_id:str,request:Request):
    return await _post(request,org_id,AnalogBody,
        lambda db,token,org,key,data:link_analog(db,token,org,data.locationId,
           key,data.leftSkuId,data.rightSkuId,data.evidenceNote))


@router.post(ROOT+"/merges")
async def post_merge(org_id:str,request:Request):
    return await _post(request,org_id,MergeBody,
        lambda db,token,org,key,data:logical_merge(db,token,org,data.locationId,
           key,data.sourceSkuId,data.targetSkuId))


@router.post(ROOT+"/imports/preview")
async def preview_import(org_id:str,request:Request,locationId:str):
    prepared=_prepare(request,org_id)
    if isinstance(prepared,JSONResponse):
        return prepared
    dsn,org=prepared
    try:
        loc=UUID(locationId)
        key=_key(request)
        if request.headers.get("content-type","").split(";",1)[0]!=(
           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
            raise ValueError("mime")
    except (ValueError,TypeError,KeyError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        if not _mutation_ok(request,db):
            return _response(request,403,"FORBIDDEN")
        # Stream with a hard limit; do not allocate attacker-sized uploads.
        chunks=[]
        size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>MAX_BYTES:
                return _response(request,422,"INVALID_INPUT")
            chunks.append(chunk)
        raw=b"".join(chunks)
        try:
            result=catalog_preview(db,request.cookies.get("rp_session",""),
                                   org,loc,key,raw)
        except (CatalogInvalid,CatalogDenied,CatalogConflict) as exc:
            return _error(request,exc)
        except _pg_conflict():
            return _response(request,409,"CONFLICT")
    return _json(result,201)


@router.post(ROOT+"/imports/commit")
async def commit_import(org_id:str,request:Request):
    return await _post(request,org_id,ImportBody,
        lambda db,token,org,key,data:catalog_commit(db,token,org,data.locationId,
               key,data.previewId,data.sha256))


@router.get(ROOT+"/export")
def export_catalog(org_id:str,request:Request,locationId:str):
    prepared=_prepare(request,org_id)
    if isinstance(prepared,JSONResponse):
        return prepared
    dsn,org=prepared
    try:
        loc=UUID(locationId)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            data=catalog_xlsx_export(db,request.cookies.get("rp_session",""),org,loc)
        except (CatalogInvalid,CatalogDenied,CatalogConflict) as exc:
            return _error(request,exc)
    return Response(content=data,media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition":'attachment; filename="auto-catalog-dev.xlsx"',
                 "Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
