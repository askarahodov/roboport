"""Synthetic closed-order reconciliation, not revenue, COGS or payroll (#448)."""
from __future__ import annotations

import csv
from io import StringIO
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_document_access import DocumentPolicyDenied, require_document_access
from .auto_org_authority import organization_role_allows
from .auto_material_cost_evidence import _period, MaterialAuditInvalid
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/reports/closed-orders"
MAX_ORDERS = 200
MAX_CASH_EVENTS = 500
MAX_SAFE = 9_007_199_254_740_991
BASIS = "CLOSED_TECHNICAL_RECONCILED_PREVIEW_AND_CASH"


class Denied(ValueError):
    pass


class Conflict(ValueError):
    pass


def report(conn, token, org, loc, from_day, to_day):
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                        for_mutation=True):
            raise Denied()
        try:
            for document, roles, cap in (
                ("FINANCE", ("MANAGER", "CASHIER"), "READ"),
                ("FINANCE", ("MANAGER", "CASHIER"), "PRICE_SALES"),
                ("ESTIMATE", ("MANAGER",), "READ"),
                ("ESTIMATE", ("MANAGER",), "PRICE_SALES"),
                ("HANDOVER", ("MANAGER", "WAREHOUSE"), "READ"),
                ("HANDOVER", ("MANAGER", "WAREHOUSE"), "PRICE_SALES"),
            ):
                # Любое ограничение истории запрещает весь филиальный отчёт:
                # частичный итог нельзя выдавать за полный.
                if require_document_access(conn, token, org, loc, roles,
                                           document, cap) is not None:
                    raise Denied()
        except DocumentPolicyDenied:
            raise Denied() from None
        branch = conn.execute("""
          SELECT timezone_name FROM auto_service_location
          WHERE id=%s AND organization_id=%s AND disabled_at IS NULL FOR SHARE
        """, (loc, org)).fetchone()
        if branch is None:
            raise Denied()
        try:
            zone = ZoneInfo(branch[0])
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise Denied() from None
        start, end = _period(from_day, to_day, zone)
        # Один SQL snapshot. Сначала ограничиваем закрытые наряды, затем
        # события расчёта. Не смешиваем безвалютный журнал CHARGE/CREDIT.
        rows = conn.execute("""
          WITH scoped AS MATERIALIZED (
            SELECT w.id AS order_id, c.id AS close_id, c.created_at AS closed_at,
                   p.id AS preview_id, p.revision_id, p.snapshot,
                   c.preview_digest, p.fingerprint,
                   posted.id AS post_id, ack.id AS ack_id
            FROM auto_order_demo_document_event c
            JOIN auto_work_order w ON w.id=c.work_order_id
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
              AND proof.vehicle_id=a.vehicle_id AND proof.verified_at IS NOT NULL
              AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
            JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
              AND binding.location_id=w.location_id
            JOIN auto_order_demo_handover_preview p ON p.id=c.preview_id
              AND p.work_order_id=w.id
            JOIN auto_order_demo_document_event posted ON posted.work_order_id=w.id
              AND posted.action='POSTED' AND posted.preview_id=p.id
              AND posted.preview_digest=c.preview_digest
              AND posted.intake_act_id=c.intake_act_id AND posted.intake_digest=c.intake_digest
            JOIN auto_order_demo_document_event ack ON ack.work_order_id=w.id
              AND ack.action='CLIENT_ACK' AND ack.preview_id=p.id
              AND ack.preview_digest=c.preview_digest
              AND ack.intake_act_id=c.intake_act_id AND ack.intake_digest=c.intake_digest
            WHERE w.organization_id=%s AND w.location_id=%s
              AND c.action='CLOSED_TECHNICAL'
              AND c.created_at >= %s AND c.created_at < %s
              AND (SELECT q.result FROM auto_work_order_qc_event q
                   WHERE q.work_order_id=w.id ORDER BY q.created_at DESC,q.id DESC LIMIT 1)='PASS'
            ORDER BY c.created_at,c.id LIMIT %s
          )
          SELECT s.order_id,s.close_id,s.closed_at,s.preview_id,s.revision_id,
                 s.snapshot,s.preview_digest,s.post_id,s.ack_id,
                 (btrim(s.fingerprint)=s.preview_digest AND
                  encode(sha256(convert_to(s.snapshot::text,'UTF8')),'hex')=s.preview_digest),
                 cash.events
          FROM scoped s
          CROSS JOIN LATERAL (
            SELECT COALESCE(jsonb_agg(jsonb_build_object(
              'id',e.id,'kind',e.kind,'amountMinor',e.amount_minor,
              'revisionId',e.revision_id) ORDER BY e.created_at,e.id),'[]'::jsonb) AS events
            FROM (
              SELECT id,kind,amount_minor,revision_id,created_at
              FROM auto_order_demo_cash_event WHERE work_order_id=s.order_id
              ORDER BY created_at,id LIMIT %s
            ) e
          ) cash
          ORDER BY s.closed_at,s.close_id
        """, (org, loc, start, end, MAX_ORDERS+1, MAX_CASH_EVENTS+1)).fetchall()
        if len(rows)>MAX_ORDERS:
            raise Conflict()
        items=[]
        total_cash=0
        for order,close,closed,preview,revision,snapshot,preview_digest,posted,ack,valid,cash in rows:
            if not valid or snapshot.get("kind")!="SYNTHETIC_PRE_HANDOVER_PREVIEW":
                raise Conflict()
            keys=("approvedBaseMinor","approvedExtraMinor","approvedTotalMinor",
                  "simulatedPaidMinor","simulatedRefundedMinor","simulatedDueMinor")
            if any(type(snapshot.get(key)) is not int or not 0<=snapshot[key]<=MAX_SAFE
                   for key in keys):
                raise Conflict()
            base,extra,approved,paid,refund,due=(snapshot[key] for key in keys)
            total_cash+=len(cash)
            if total_cash>MAX_CASH_EVENTS:
                raise Conflict()
            if any(x["revisionId"]!=str(revision) or x["kind"] not in
                   ("SIM_PAYMENT","SIM_REFUND") or type(x["amountMinor"]) is not int
                   or not 0<x["amountMinor"]<=MAX_SAFE for x in cash):
                raise Conflict()
            if (base+extra!=approved or paid-refund!=approved or due!=0
                or paid!=sum(x["amountMinor"] for x in cash if x["kind"]=="SIM_PAYMENT")
                or refund!=sum(x["amountMinor"] for x in cash if x["kind"]=="SIM_REFUND")
                or snapshot.get("currency")!="RUB"
                or snapshot.get("releaseAllowed") is not False
                or snapshot.get("legalGatePassed") is not False):
                raise Conflict()
            items.append({
                "workOrderId":str(order),"closedDay":closed.astimezone(zone).date().isoformat(),
                "previewId":str(preview),"previewDigest":preview_digest,
                "estimateRevisionId":str(revision),"postedEventId":str(posted),
                "ackEventId":str(ack),"closedEventId":str(close),
                "approvedBaseMinor":base,"approvedExtraMinor":extra,"approvedTotalMinor":approved,
                "simulatedPaidMinor":paid,"simulatedRefundedMinor":refund,
                "cashEvents":cash,
            })
        summary={key:sum(item[key] for item in items) for key in (
            "approvedBaseMinor","approvedExtraMinor","approvedTotalMinor",
            "simulatedPaidMinor","simulatedRefundedMinor")}
        if any(n>MAX_SAFE for n in summary.values()):
            raise Conflict()
        return {"syntheticOnly":True,"realMoney":False,"releaseAllowed":False,
                "marginAvailable":False,"payrollAvailable":False,"currency":"RUB",
                "basis":BASIS,"timeZone":branch[0],"fromDay":from_day,"toDay":to_day,
                "summary":{"orderCount":len(items),"cashEventCount":total_cash,**summary},
                "items":items}


def _csv(result):
    output=StringIO(newline="")
    writer=csv.writer(output,delimiter=";",lineterminator="\r\n")
    writer.writerow(("Заказ-наряд UUID","День закрытия","Смета UUID","Пакет UUID",
                     "SHA-256","Проведение UUID","Подтверждение UUID","Закрытие UUID",
                     "Основная смета, коп.","Допработы, коп.","Утверждено, коп.",
                     "Имитации оплат, коп.","Имитации возвратов, коп.",
                     "Событие расчёта UUID","Вид события","Сумма события, коп."))
    for x in result["items"]:
        # Итоги заказа пишутся ровно один раз в отдельной строке.
        writer.writerow((x["workOrderId"],x["closedDay"],x["estimateRevisionId"],
                         x["previewId"],x["previewDigest"],x["postedEventId"],
                         x["ackEventId"],x["closedEventId"],x["approvedBaseMinor"],
                         x["approvedExtraMinor"],x["approvedTotalMinor"],
                         x["simulatedPaidMinor"],x["simulatedRefundedMinor"],"","",""))
        for e in x["cashEvents"]:
            writer.writerow((x["workOrderId"],x["closedDay"],"","","","","","","","","","","",
                             e["id"],e["kind"],e["amountMinor"]))
    return "\ufeff"+output.getvalue()


def _run(request,org_id,loc_id,from_day,to_day):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
        with psycopg.connect(dsn) as conn:
            return report(conn,request.cookies.get("rp_session",""),org,loc,from_day,to_day)
    except Denied:
        return _response(request,403,"FORBIDDEN")
    except Conflict:
        return _response(request,409,"REPORT_CONFLICT")
    except (ValueError,MaterialAuditInvalid):
        return _response(request,422,"INVALID_INPUT")


@router.get(PREFIX)
def get_closed_orders(org_id:str,loc_id:str,request:Request,fromDay:str,toDay:str):
    result=_run(request,org_id,loc_id,fromDay,toDay)
    return result if isinstance(result,JSONResponse) else JSONResponse(
        content=result,headers={"Cache-Control":"no-store"})


@router.get(PREFIX+"/export")
def export_closed_orders(org_id:str,loc_id:str,request:Request,fromDay:str,toDay:str):
    result=_run(request,org_id,loc_id,fromDay,toDay)
    return result if isinstance(result,JSONResponse) else Response(
        content=_csv(result),media_type="text/csv; charset=utf-8",
        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                 "Content-Disposition":'attachment; filename="auto-closed-order-report.csv"'})
