"""Synthetic branch warehouse ABC/XYZ based on immutable quantity ISSUE events.

ABC: rank by gross issued stock units, not monetary value or profit.
XYZ: coefficient of variation of monthly gross issued units over 3..12
fully completed branch-local calendar months. RETURN is reported separately.
No customer, work-order, supplier, employee or price data leaves this API.
"""
from datetime import datetime,timezone
from math import isqrt
from uuid import UUID
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse

from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn,_response

router=APIRouter(include_in_schema=False)
PAGE_SIZE=25
MAX_SKUS=10000


class StockReportDenied(ValueError):
    pass


class StockReportInvalid(ValueError):
    pass


class StockReportCapacity(ValueError):
    pass


def _month(value):
    if (type(value) is not str or len(value)!=7 or value[4]!='-' or
        not value[:4].isdigit() or not value[5:].isdigit()):
        raise StockReportInvalid("INVALID_MONTH")
    year,month=int(value[:4]),int(value[5:])
    if year<2000 or year>9998 or month<1 or month>12:
        raise StockReportInvalid("INVALID_MONTH")
    return year*12+month-1


def _month_string(month_index):
    year,month=divmod(month_index,12)
    return f"{year:04d}-{month+1:02d}"


def _start_of_month(month_index,zone):
    year,month=divmod(month_index,12)
    return datetime(year,month+1,1,tzinfo=zone).astimezone(timezone.utc)


def _variation(monthly):
    """Return integer CV basis points and X/Y/Z from exact rational bounds."""
    total=sum(monthly)
    if total==0:
        return None,None
    n=len(monthly)
    numerator=n*sum(x*x for x in monthly)-total*total
    # CV^2 = (n * sum(q_i^2) - sum(q_i)^2) / sum(q_i)^2.
    # Boundaries: X <= 10%, Y <= 25%, Z otherwise. Exact integer comparisons.
    if numerator*100<=total*total:
        classification="X"
    elif numerator*16<=total*total:
        classification="Y"
    else:
        classification="Z"
    variation=isqrt(numerator*100_000_000//(total*total))
    return variation,classification


def stock_abc_xyz(conn:psycopg.Connection,token:str,org:UUID,loc:UUID,
                  first_month:str,last_month:str,offset:int=0):
    if (type(org) is not UUID or type(loc) is not UUID or
        type(offset) is not int or offset<0 or offset>1_000_000 or
        offset%PAGE_SIZE):
        raise StockReportInvalid("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn,token,org,"MANAGER",loc,for_mutation=True,
        ):
            raise StockReportDenied("FORBIDDEN")
        branch=conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",(loc,org),
        ).fetchone()
        if branch is None:
            raise StockReportDenied("FORBIDDEN")
        try:
            zone=ZoneInfo(branch[0])
        except (ZoneInfoNotFoundError,ValueError,TypeError):
            raise StockReportDenied("FORBIDDEN") from None
        first,last=_month(first_month),_month(last_month)
        n=last-first+1
        local_now=datetime.now(zone)
        current=local_now.year*12+local_now.month-1
        if not 3<=n<=12 or last>=current:
            raise StockReportInvalid("PERIOD_MUST_USE_3_TO_12_CLOSED_MONTHS")
        months=[_month_string(index) for index in range(first,last+1)]
        begin,end=_start_of_month(first,zone),_start_of_month(last+1,zone)
        # One SQL snapshot for all SKUs and all movement events; include
        # zero-use SKUs without amplifying other warehouse event classes.
        # SKU article is synthetic-only; no customer, order or vendor joins.
        rows=conn.execute(
            """SELECT sku.id,sku.article,
                      to_char(event.created_at AT TIME ZONE %s,'YYYY-MM') AS local_month,
                      COALESCE(SUM(event.quantity) FILTER(
                        WHERE event.action='ISSUE'),0)::bigint AS issued,
                      COALESCE(SUM(event.quantity) FILTER(
                        WHERE event.action='RETURN'),0)::bigint AS returned
               FROM auto_stock_sku sku
               JOIN auto_service_location location
                 ON location.id=sku.location_id
                 AND location.organization_id=sku.organization_id
                 AND location.disabled_at IS NULL
               JOIN auto_organization organization
                 ON organization.id=location.organization_id
                 AND organization.disabled_at IS NULL
               LEFT JOIN auto_stock_event event
                 ON event.sku_id=sku.id
                 AND event.organization_id=sku.organization_id
                 AND event.location_id=sku.location_id
                 AND event.action IN ('ISSUE','RETURN')
                 AND event.created_at>=%s AND event.created_at<%s
               WHERE sku.organization_id=%s AND sku.location_id=%s
               GROUP BY 1,2,3
               ORDER BY sku.id,local_month""",
            (branch[0],begin,end,org,loc),
        ).fetchall()
        detail={}
        for sku,article,month,issued,returned in rows:
            key=str(sku)
            if key not in detail:
                if len(detail)>=MAX_SKUS:
                    raise StockReportCapacity("too many SKUs for one report")
                detail[key]={"skuId":key,"article":article,
                             "grossIssued":0,"grossReturned":0,
                             "monthlyIssued":[0]*n}
            row=detail[key]
            if month is not None:
                index=_month(month)-first
                if not 0<=index<n:
                    raise StockReportInvalid("OUTSIDE_WINDOW")
                row["monthlyIssued"][index]=int(issued)
                row["grossIssued"]+=int(issued)
                row["grossReturned"]+=int(returned)
        ranked=sorted(detail.values(),key=lambda row:
                      (-row["grossIssued"],row["article"],row["skuId"]))
        total_issued=sum(row["grossIssued"] for row in ranked)
        total_returned=sum(row["grossReturned"] for row in ranked)
        counts={"A":0,"B":0,"C":0,"X":0,"Y":0,"Z":0,"none":0}
        before=0
        for row in ranked:
            quantity=row["grossIssued"]
            row["netPeriod"]=quantity-row["grossReturned"]
            if quantity==0:
                row["abc"]=None
                row["xyz"]=None
                row["variationBasisPoints"]=None
                counts["none"]+=1
                continue
            row["abc"]=("A" if before*100<total_issued*80 else
                        "B" if before*100<total_issued*95 else "C")
            before+=quantity
            row["variationBasisPoints"],row["xyz"]=_variation(row["monthlyIssued"])
            counts[row["abc"]]+=1
            counts[row["xyz"]]+=1
        page=ranked[offset:offset+PAGE_SIZE]
        next_offset=(offset+PAGE_SIZE if offset+PAGE_SIZE<len(ranked) else None)
        return {
            "syntheticOnly":True,
            "basis":"GROSS_ISSUE_UNITS_CLOSED_MONTHS",
            "moneyValuation":False,
            "timeZone":branch[0],
            "fromMonth":first_month,"toMonth":last_month,
            "months":months,"pageSize":PAGE_SIZE,
            "offset":offset,"nextOffset":next_offset,
            "summary":{
                "skuCount":len(ranked),
                "activeSkus":len(ranked)-counts["none"],
                "grossIssued":total_issued,
                "grossReturned":total_returned,
                "netPeriod":total_issued-total_returned,
                "classCounts":counts,
            },
            "items":page,
        }


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/stock-abc-xyz")
def get_stock_abc_xyz(org_id:str,loc_id:str,request:Request,
                      fromMonth:str,toMonth:str,offset:int=0):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as db:
            result=stock_abc_xyz(
                db,request.cookies.get("rp_session",""),org,loc,
                fromMonth,toMonth,offset,
            )
    except StockReportDenied:
        return _response(request,403,"FORBIDDEN")
    except StockReportInvalid:
        return _response(request,422,"INVALID_INPUT")
    except StockReportCapacity:
        return _response(request,409,"REPORT_LIMIT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
