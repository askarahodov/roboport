"""Manager-only synthetic operational ledger comparison, never real cash/turnover.

Two explicitly independent journals: currency-less CHARGE/CREDIT corrections
and RUB-denominated DEMO simulated settlements. No addition across ledgers.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auto_bay_occupancy import _day, OccupancyInvalid
from .auto_org_authority import organization_role_allows
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_vehicle_claim_http import _request_dsn, _response


router = APIRouter(include_in_schema=False)


class FinanceReportDenied(ValueError):
    pass


class FinanceReportInvalid(ValueError):
    pass


def daily_demo_finance(conn: psycopg.Connection, token: str, *,
                       organization_id: UUID, location_id: UUID,
                       from_day: str, to_day: str) -> dict:
    if any(type(x) is not UUID for x in (organization_id,location_id)):
        raise FinanceReportInvalid("INVALID_SCOPE")
    try:
        first,last=_day(from_day),_day(to_day)
    except OccupancyInvalid:
        raise FinanceReportInvalid("INVALID_DATE") from None
    length=(last-first).days+1
    if not 1<=length<=31 or last.year==9999:
        raise FinanceReportInvalid("INVALID_RANGE")
    with conn.transaction():
        if not organization_role_allows(
            conn,token,organization_id,"MANAGER",location_id,for_mutation=True
        ):
            raise FinanceReportDenied("FORBIDDEN")
        try:
            for cap in ("READ", "PRICE_SALES"):
                require_document_access(conn,token,organization_id,location_id,
                                        ("MANAGER",),"FINANCE",cap)
        except DocumentPolicyDenied:
            raise FinanceReportDenied("FORBIDDEN") from None
        location=conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",
            (location_id,organization_id),
        ).fetchone()
        if location is None:
            raise FinanceReportDenied("FORBIDDEN")
        try:
            zone=ZoneInfo(location[0])
        except (ZoneInfoNotFoundError,ValueError):
            raise FinanceReportDenied("FORBIDDEN") from None
        items=[]
        for index in range(length):
            day=first+timedelta(days=index)
            tomorrow=day+timedelta(days=1)
            start=datetime(day.year,day.month,day.day,tzinfo=zone)
            end=datetime(tomorrow.year,tomorrow.month,tomorrow.day,tzinfo=zone)
            charge_count,charge_minor,credit_count,credit_minor=conn.execute(
                """SELECT COUNT(*) FILTER(WHERE e.action='CHARGE')::bigint,
                          COALESCE(SUM(e.amount_minor) FILTER(
                            WHERE e.action='CHARGE'),0)::bigint,
                          COUNT(*) FILTER(WHERE e.action='CREDIT')::bigint,
                          COALESCE(SUM(e.amount_minor) FILTER(
                            WHERE e.action='CREDIT'),0)::bigint
                   FROM auto_work_order_financial_event e
                   JOIN auto_work_order w ON w.id=e.work_order_id
                     AND w.organization_id=e.organization_id
                     AND w.location_id=e.location_id
                   WHERE e.organization_id=%s AND e.location_id=%s
                     AND e.created_at >= %s AND e.created_at < %s""",
                (organization_id,location_id,start,end),
            ).fetchone()
            pay_count,pay_minor,refund_count,refund_minor=conn.execute(
                """SELECT COUNT(*) FILTER(WHERE c.kind='SIM_PAYMENT')::bigint,
                          COALESCE(SUM(c.amount_minor) FILTER(
                            WHERE c.kind='SIM_PAYMENT'),0)::bigint,
                          COUNT(*) FILTER(WHERE c.kind='SIM_REFUND')::bigint,
                          COALESCE(SUM(c.amount_minor) FILTER(
                            WHERE c.kind='SIM_REFUND'),0)::bigint
                   FROM auto_order_demo_cash_event c
                   JOIN auto_work_order w ON w.id=c.work_order_id
                   WHERE w.organization_id=%s AND w.location_id=%s
                     AND c.created_at >= %s AND c.created_at < %s""",
                (organization_id,location_id,start,end),
            ).fetchone()
            sealed=conn.execute(
                """SELECT COUNT(*)::bigint
                   FROM auto_order_demo_handover_preview p
                   JOIN auto_work_order w ON w.id=p.work_order_id
                   WHERE w.organization_id=%s AND w.location_id=%s
                     AND p.created_at >= %s AND p.created_at < %s""",
                (organization_id,location_id,start,end),
            ).fetchone()[0]
            items.append({
                "localDay":day.isoformat(),
                "chargeEvents":charge_count,
                "chargeMinor":charge_minor,
                "creditEvents":credit_count,
                "creditMinor":credit_minor,
                "correctionNetMinor":charge_minor-credit_minor,
                "demoPaymentEvents":pay_count,
                "demoPaymentMinor":pay_minor,
                "demoRefundEvents":refund_count,
                "demoRefundMinor":refund_minor,
                "demoNetMinor":pay_minor-refund_minor,
                "sealedPreviewCount":sealed,
            })
        return {
            "syntheticOnly":True,"legalGatePassed":False,
            "releaseAllowed":False,"financeCurrency":None,
            "demoCurrency":"RUB","timeZone":location[0],
            "basis":"INDEPENDENT_LEDGER_EVENT_LOCAL_DAYS",
            "items":items,
        }


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/demo-finance-report")
def demo_finance_report(org_id: str,loc_id: str,request: Request,
                        fromDay: str,toDay: str):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        organization=UUID(org_id)
        location=UUID(loc_id)
        with psycopg.connect(dsn) as db:
            result=daily_demo_finance(
                db,request.cookies.get("rp_session",""),
                organization_id=organization,location_id=location,
                from_day=fromDay,to_day=toDay,
            )
    except FinanceReportDenied:
        return _response(request,403,"FORBIDDEN")
    except (ValueError,FinanceReportInvalid):
        return _response(request,422,"INVALID_INPUT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})
