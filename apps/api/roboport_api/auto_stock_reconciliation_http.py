"""Закрытый read-only отчёт сверки склада."""
from uuid import UUID
import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse
from .auto_vehicle_claim_http import _request_dsn,_response
from .auto_stock_service import AutoStockDenied
from .auto_stock_reconciliation import stock_report,stock_detail

router=APIRouter(include_in_schema=False)
PREFIX="/api/v1/dev/auto/organizations/{org_id}/warehouse/reconciliation"


def _read(org_id,request,sku_id=None):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org=UUID(org_id)
        names=("locationId","kind","cursor") if sku_id is not None else ("locationId","q","status","cursor")
        fields={name:request.query_params.getlist(name) for name in names}
        if any(len(v)>1 for v in fields.values()) or len(fields["locationId"])!=1 or fields["cursor"]==[""]:
            raise ValueError("scope")
        loc=UUID(fields["locationId"][0])
        def value(name,default=None):
            return fields[name][0] if fields[name] else default
        sku=UUID(sku_id) if sku_id is not None else None
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    with psycopg.connect(dsn) as db:
        try:
            token=request.cookies.get("rp_session","")
            result=(stock_detail(db,token,org,loc,sku,value("kind","lots"),value("cursor"))
                    if sku is not None else
                    stock_report(db,token,org,loc,value("q",""),value("status","ALL"),value("cursor")))
        except AutoStockDenied as exc:
            code="INVALID_INPUT" if str(exc)=="INVALID_INPUT" else "FORBIDDEN"
            return _response(request,422 if code=="INVALID_INPUT" else 403,code)
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get(PREFIX)
def report(org_id:str,request:Request):
    return _read(org_id,request)


@router.get(PREFIX+"/{sku_id}")
def detail(org_id:str,sku_id:str,request:Request):
    return _read(org_id,request,sku_id)
