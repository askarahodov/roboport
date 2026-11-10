"""Branch-only CRM → completed technical WorkOrder → SIM settlement evidence.

This is NOT revenue: only synthetic closed technical documents and synthetic
treasury events are counted. No customer IDs, prices of individual orders,
person information or supplier data leaves this aggregate endpoint.
"""
from datetime import datetime, date, timedelta, timezone
from io import StringIO
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import csv

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_org_authority import organization_role_allows
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_crm_funnel import _day, FunnelInvalid

router = APIRouter(include_in_schema=False)
SOURCES = ("WEB", "PHONE", "WALK_IN")
BASIS = "LEAD_COHORT_CLOSED_TECHNICAL_SIM_TREASURY"


class OutcomeDenied(ValueError):
    pass


class OutcomeInvalid(ValueError):
    pass


def _blank(source):
    return {
        "sourceCode": source, "bookedLeads": 0, "closedTechnical": 0,
        "simSettledMinor": "0", "simRefundMinor": "0",
    }


def crm_outcomes(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                 from_day: str, to_day: str) -> dict:
    if type(org) is not UUID or type(loc) is not UUID:
        raise OutcomeInvalid("INVALID_SCOPE")
    try:
        first, last = _day(from_day), _day(to_day)
    except FunnelInvalid:
        raise OutcomeInvalid("INVALID_DAY") from None
    if not 1 <= (last - first).days + 1 <= 31 or last == date.max:
        raise OutcomeInvalid("INVALID_RANGE")
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                        for_mutation=True):
            raise OutcomeDenied("FORBIDDEN")
        # Monetary aggregates are forbidden when a granular sales-price
        # policy denies access, even when the caller remains a MANAGER.
        try:
            estimate_cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER",), "ESTIMATE", "READ")
            require_document_access(
                conn, token, org, loc, ("MANAGER",), "ESTIMATE", "PRICE_SALES")
            handover_cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER",), "HANDOVER", "READ")
            require_document_access(
                conn, token, org, loc, ("MANAGER",), "HANDOVER", "PRICE_SALES")
            finance_cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER","CASHIER"), "FINANCE", "READ")
            require_document_access(
                conn, token, org, loc, ("MANAGER","CASHIER"), "FINANCE", "PRICE_SALES")
        except DocumentPolicyDenied:
            raise OutcomeDenied("FORBIDDEN") from None

        branch = conn.execute("""
            SELECT timezone_name FROM auto_service_location
            WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
            FOR SHARE
        """, (loc, org)).fetchone()
        if branch is None:
            raise OutcomeDenied("FORBIDDEN")
        try:
            zone = ZoneInfo(branch[0])
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise OutcomeDenied("BAD_TIMEZONE") from None
        start = datetime(first.year, first.month, first.day, tzinfo=zone
                         ).astimezone(timezone.utc)
        day_after = last + timedelta(days=1)
        end = datetime(day_after.year, day_after.month, day_after.day,
                       tzinfo=zone).astimezone(timezone.utc)
        # Financial policy applies to the SIM columns too. An active
        # historical cutoff excludes those orders from attribution rather
        # than exposing their prior balances via the CRM aggregate.
        cutoffs = [x for x in (estimate_cutoff, handover_cutoff, finance_cutoff)
                   if x is not None]
        min_order_created = max(cutoffs) if cutoffs else None

        # The live verified link and branch are checked BEFORE aggregation.
        # Resolve the latest appointment booking after any reschedule: the
        # original a.booking_id remains immutable, but is canceled by move.
        # One appointment may be claimed by multiple leads, or multiple
        # WorkOrders: such ambiguous attribution must NEVER allocate money.
        # CASH_SIM / BANK_SIM events are not real revenue or posted money.
        rows = conn.execute("""
            WITH cohort AS (
              SELECT lead.source_code, lead.organization_id, lead.location_id,
                     lead.customer_link_id, stage.appointment_id
              FROM auto_crm_lead lead
              JOIN LATERAL (
                SELECT stage_code, appointment_id
                FROM auto_crm_lead_stage event
                WHERE event.lead_id=lead.id
                ORDER BY event.created_at DESC, event.id DESC LIMIT 1
              ) stage ON stage.stage_code='BOOKED'
              WHERE lead.organization_id=%s AND lead.location_id=%s
                AND lead.created_at >= %s AND lead.created_at < %s
                AND EXISTS (
                  SELECT 1 FROM auto_vehicle_customer_link proof
                  JOIN auto_vehicle_location_binding binding
                    ON binding.vehicle_id=proof.vehicle_id
                      AND binding.location_id=lead.location_id
                  JOIN auto_service_location location
                    ON location.id=binding.location_id
                     AND location.organization_id=lead.organization_id
                     AND location.disabled_at IS NULL
                  JOIN auto_organization tenant
                    ON tenant.id=location.organization_id
                     AND tenant.disabled_at IS NULL
                  WHERE proof.id=lead.customer_link_id
                    AND proof.verified_at IS NOT NULL
                    AND proof.verified_by IS NOT NULL
                    AND proof.revoked_at IS NULL
                  FOR SHARE OF proof,location,tenant
                )
            )
            SELECT cohort.source_code, COUNT(*)::bigint,
                   COUNT(w.id)::bigint,
                   COALESCE(SUM(money.settled),0)::text,
                   COALESCE(SUM(money.refunded),0)::text
            FROM cohort
            LEFT JOIN auto_appointment a
              ON a.id=cohort.appointment_id
             AND a.organization_id=cohort.organization_id
             AND a.location_id=cohort.location_id
             AND a.customer_link_id=cohort.customer_link_id
            LEFT JOIN LATERAL (
              SELECT COALESCE((
                SELECT reschedule.new_booking_id
                FROM auto_appointment_reschedule reschedule
                WHERE reschedule.appointment_id=a.id
                ORDER BY reschedule.created_at DESC,reschedule.id DESC LIMIT 1
              ),a.booking_id) AS current_id
            ) current_booking ON a.id IS NOT NULL
            LEFT JOIN auto_bay_booking booking
              ON booking.id=current_booking.current_id
             AND booking.organization_id=cohort.organization_id
             AND booking.location_id=cohort.location_id
             AND booking.canceled_at IS NULL
            LEFT JOIN auto_work_order w
              ON w.appointment_id=a.id
             AND booking.id IS NOT NULL
             AND w.organization_id=cohort.organization_id
             AND w.location_id=cohort.location_id
             AND (%s::timestamptz IS NULL OR w.created_at >= %s::timestamptz)
             AND (SELECT COUNT(*) FROM auto_work_order x
                  WHERE x.appointment_id=a.id)=1
             AND (SELECT COUNT(*) FROM auto_crm_lead_stage x
                  WHERE x.appointment_id=a.id AND x.stage_code='BOOKED')=1
             AND EXISTS (SELECT 1 FROM auto_order_demo_document_event posted
                 WHERE posted.work_order_id=w.id AND posted.action='POSTED')
             AND EXISTS (SELECT 1 FROM auto_order_demo_document_event closed
                 WHERE closed.work_order_id=w.id
                   AND closed.action='CLOSED_TECHNICAL')
            LEFT JOIN LATERAL (
              SELECT COALESCE(SUM(CASE
                       WHEN e.kind IN ('PAYMENT','APPLY_ADVANCE') THEN e.amount_minor
                       WHEN e.kind='REFUND_PAYMENT' THEN -e.amount_minor
                       ELSE 0 END),0)::numeric AS settled,
                     COALESCE(SUM(CASE WHEN e.kind='REFUND_PAYMENT'
                       THEN e.amount_minor ELSE 0 END),0)::numeric AS refunded
              FROM auto_treasury_event e
              JOIN auto_treasury_account account ON account.id=e.account_id
               AND account.organization_id=cohort.organization_id
               AND account.location_id=cohort.location_id
               AND account.kind IN ('CASH_SIM','BANK_SIM')
              WHERE e.work_order_id=w.id
            ) money ON w.id IS NOT NULL
            GROUP BY cohort.source_code
        """, (org, loc, start, end,
              min_order_created, min_order_created)).fetchall()

        summary = _blank("ALL")
        by_source = {source: _blank(source) for source in SOURCES}
        for source, booked, closed, settled, refunded in rows:
            if source not in by_source or not (0 <= closed <= booked):
                raise OutcomeInvalid("CORRUPTED_SOURCE")
            values = (int(booked), int(closed), int(settled), int(refunded))
            if any(value < 0 for value in values):
                raise OutcomeInvalid("CORRUPTED_TOTAL")
            row = by_source[source]
            row.update(bookedLeads=values[0], closedTechnical=values[1],
                       simSettledMinor=str(values[2]),
                       simRefundMinor=str(values[3]))
            summary["bookedLeads"] += values[0]
            summary["closedTechnical"] += values[1]
            summary["simSettledMinor"] = str(
                int(summary["simSettledMinor"]) + values[2])
            summary["simRefundMinor"] = str(
                int(summary["simRefundMinor"]) + values[3])

        return {
            "syntheticOnly": True, "realMoney": False,
            "releaseAllowed": False, "revenueAvailable": False,
            "basis": BASIS, "currency": "RUB",
            "timeZone": branch[0], "fromDay": from_day, "toDay": to_day,
            "summary": summary, "items": [by_source[x] for x in SOURCES],
        }


def _csv(report: dict) -> str:
    names = {"ALL": "Все источники", "WEB": "Сайт",
             "PHONE": "Звонок", "WALK_IN": "Личное обращение"}
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Источник", "Записанных лидов",
                     "Технически закрытых заказ-нарядов",
                     "Синтетический зачёт, коп. (НЕ выручка)",
                     "Синтетические возвраты, коп."))
    for row in (report["summary"], *report["items"]):
        writer.writerow((names[row["sourceCode"]], row["bookedLeads"],
                         row["closedTechnical"], row["simSettledMinor"],
                         row["simRefundMinor"]))
    return "\ufeff" + output.getvalue()


def _run(request: Request, org_id: str, loc_id: str,
         fromDay: str, toDay: str):
    config = _request_dsn(request)
    if isinstance(config, JSONResponse):
        return config
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(config) as conn:
            return crm_outcomes(conn, request.cookies.get("rp_session", ""),
                                org, loc, fromDay, toDay)
    except OutcomeDenied:
        return _response(request, 403, "FORBIDDEN")
    except OutcomeInvalid:
        return _response(request, 422, "INVALID_INPUT")


PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/outcomes"


@router.get(PREFIX)
def get_outcomes(org_id: str, loc_id: str, request: Request,
                 fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get(PREFIX + "/export")
def export_outcomes(org_id: str, loc_id: str, request: Request,
                    fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    return Response(content=_csv(result), media_type="text/csv; charset=utf-8",
                    headers={"Cache-Control": "no-store",
                             "X-Content-Type-Options": "nosniff",
                             "Content-Disposition":
                             'attachment; filename="auto-crm-outcomes.csv"'})
