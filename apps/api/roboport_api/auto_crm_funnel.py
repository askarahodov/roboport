"""Read-only synthetic CRM lead-cohort funnel for a branch MANAGER.

A cohort is defined by the local date when an immutable lead was CREATED;
its stage is the CURRENT last recorded stage, not its state on that day.
Only live verified vehicle links are counted, and no person, vehicle, lead,
appointment, owner, price or contact identifier is returned.
"""
import csv
from io import StringIO
from datetime import date,datetime,timedelta,timezone
from uuid import UUID
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter,Request
from fastapi.responses import JSONResponse,Response

from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn,_response

router=APIRouter(include_in_schema=False)
SOURCES=("WEB","PHONE","WALK_IN")
REASONS=("PRICE","TIMING","NO_RESPONSE","OTHER")
STAGES=("NEW","QUALIFIED","BOOKED","LOST")


class FunnelDenied(ValueError):
    pass


class FunnelInvalid(ValueError):
    pass


def _day(value):
    if (type(value) is not str or len(value)!=10 or
            value[4]!='-' or value[7]!='-'):
        raise FunnelInvalid("invalid date")
    try:
        parsed=date.fromisoformat(value)
        if parsed.isoformat()!=value:
            raise ValueError()
        return parsed
    except ValueError:
        raise FunnelInvalid("invalid date") from None


def _blank(source):
    return {
        "sourceCode":source,"total":0,
        "new":0,"qualified":0,"booked":0,"lost":0,
        "bookedBasisPoints":None,
        "lossReasons":{reason:0 for reason in REASONS},
    }


def _add(target,stage,reason,count):
    if stage not in STAGES or (stage=="LOST")!=(reason is not None) or (
            reason is not None and reason not in REASONS
    ):
        # A corrupted source event must never yield a seemingly valid KPI.
        raise FunnelInvalid("corrupted CRM event")
    target["total"]+=count
    target[stage.lower()]+=count
    if reason is not None:
        target["lossReasons"][reason]+=count


def _finish(row):
    row["bookedBasisPoints"]=(
        row["booked"]*10000//row["total"] if row["total"] else None
    )
    return row


def crm_funnel(conn:psycopg.Connection,token:str,org:UUID,loc:UUID,
               from_day:str,to_day:str):
    if type(org) is not UUID or type(loc) is not UUID:
        raise FunnelInvalid("invalid scope")
    start_day,end_day=_day(from_day),_day(to_day)
    if not 1<=(end_day-start_day).days+1<=31 or end_day==date.max:
        raise FunnelInvalid("period must be 1..31 days")
    with conn.transaction():
        if not organization_role_allows(
            conn,token,org,"MANAGER",loc,for_mutation=True,
        ):
            raise FunnelDenied("manager branch grant required")
        row=conn.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",(loc,org),
        ).fetchone()
        if row is None:
            raise FunnelDenied("branch disabled")
        try:
            zone=ZoneInfo(row[0])
        except (ZoneInfoNotFoundError,ValueError,TypeError):
            raise FunnelDenied("invalid branch timezone") from None
        first=datetime(start_day.year,start_day.month,start_day.day,tzinfo=zone)
        after_day=end_day+timedelta(days=1)
        last=datetime(after_day.year,after_day.month,after_day.day,tzinfo=zone)
        start=first.astimezone(timezone.utc)
        end=last.astimezone(timezone.utc)

        # One statement = one MVCC read snapshot. Resolve latest immutable
        # stage per lead, and filter still-verified scoped links *before*
        # aggregation. This is a live view, not a historical stage snapshot.
        groups=conn.execute(
            """SELECT lead.source_code,
                      COALESCE(latest.stage_code,'NEW') AS current_stage,
                      latest.reason_code,COUNT(*)::bigint
               FROM auto_crm_lead lead
               LEFT JOIN LATERAL(
                 SELECT stage_code,reason_code
                 FROM auto_crm_lead_stage event
                 WHERE event.lead_id=lead.id
                 ORDER BY event.created_at DESC,event.id DESC LIMIT 1
               ) latest ON TRUE
               WHERE lead.organization_id=%s AND lead.location_id=%s
                 AND lead.created_at>=%s AND lead.created_at<%s
                 AND EXISTS(
                   SELECT 1 FROM auto_vehicle_customer_link proof
                   JOIN auto_vehicle_location_binding binding
                     ON binding.vehicle_id=proof.vehicle_id
                     AND binding.location_id=lead.location_id
                   JOIN auto_service_location branch
                     ON branch.id=binding.location_id
                     AND branch.organization_id=lead.organization_id
                     AND branch.disabled_at IS NULL
                   JOIN auto_organization tenant
                     ON tenant.id=branch.organization_id
                     AND tenant.disabled_at IS NULL
                   WHERE proof.id=lead.customer_link_id
                     AND proof.verified_at IS NOT NULL
                     AND proof.verified_by IS NOT NULL
                     AND proof.revoked_at IS NULL
                   FOR SHARE OF proof,branch,tenant
                 )
               GROUP BY lead.source_code,COALESCE(latest.stage_code,'NEW'),
                        latest.reason_code""",
            (org,loc,start,end),
        ).fetchall()
        by_source={code:_blank(code) for code in SOURCES}
        summary=_blank("ALL")
        for source,stage,reason,n in groups:
            if source not in by_source:
                raise FunnelInvalid("unknown CRM source")
            _add(by_source[source],stage,reason,int(n))
            _add(summary,stage,reason,int(n))
        return {
            "syntheticOnly":True,
            "basis":"LEAD_CREATED_COHORT_CURRENT_STAGE",
            "timeZone":row[0],
            "fromDay":from_day,"toDay":to_day,
            "summary":_finish(summary),
            "items":[_finish(by_source[code]) for code in SOURCES],
        }


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/funnel")
def get_crm_funnel(org_id:str,loc_id:str,request:Request,
                   fromDay:str,toDay:str):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as db:
            result=crm_funnel(db,request.cookies.get("rp_session",""),
                              org,loc,fromDay,toDay)
    except FunnelDenied:
        return _response(request,403,"FORBIDDEN")
    except FunnelInvalid:
        return _response(request,422,"INVALID_INPUT")
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


def _funnel_csv(report:dict)->str:
    """A complete manager-only aggregate, with no identifiers or free-text fields."""
    names={"ALL":"Все источники","WEB":"Сайт",
           "PHONE":"Звонок","WALK_IN":"Личное обращение"}
    output=StringIO(newline="")
    writer=csv.writer(output,delimiter=";",lineterminator="\r\n")
    writer.writerow(("Источник","Всего","Новое","Обработано","Записан",
                     "Отказ","Доля записанных (б.п.)","Отказ: стоимость",
                     "Отказ: сроки","Отказ: нет ответа","Отказ: прочее"))
    for row in (report["summary"],*report["items"]):
        writer.writerow((names[row["sourceCode"]],row["total"],row["new"],
                         row["qualified"],row["booked"],row["lost"],
                         row["bookedBasisPoints"] if row["bookedBasisPoints"] is not None else "",
                         *(row["lossReasons"][key] for key in REASONS)))
    return "\ufeff"+output.getvalue()


@router.get("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/funnel/export")
def export_crm_funnel(org_id:str,loc_id:str,request:Request,
                      fromDay:str,toDay:str):
    """Never export a stale browser report: recheck live grants and proof links."""
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (ValueError,TypeError):
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as db:
            report=crm_funnel(db,request.cookies.get("rp_session",""),
                              org,loc,fromDay,toDay)
    except FunnelDenied:
        return _response(request,403,"FORBIDDEN")
    except FunnelInvalid:
        return _response(request,422,"INVALID_INPUT")
    return Response(content=_funnel_csv(report),media_type="text/csv; charset=utf-8",
                    headers={"Cache-Control":"no-store",
                             "X-Content-Type-Options":"nosniff",
                             "Content-Disposition":
                             'attachment; filename="auto-crm-funnel.csv"'})
