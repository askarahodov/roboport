"""Fail-closed synthetic provenance audit of work-order lot issues/returns.

A receipt quote is NOT issue-layer cost, COGS, VAT due, margin or profit.
An immutable physical lot can contain stock from sources not covered by a
valuation. Even a single valued receipt proves only quote provenance.
"""
from __future__ import annotations

import csv
import re
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/reports/material-cost-evidence"
MAX_EVENTS = 500
BASIS = "IMMUTABLE_LOT_MOVEMENTS_RECEIPT_QUOTE_PROVENANCE"


class MaterialAuditDenied(ValueError):
    pass


class MaterialAuditInvalid(ValueError):
    pass


class MaterialAuditLimit(ValueError):
    pass


def _period(from_day: str, to_day: str, zone: ZoneInfo):
    if any(type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
           for value in (from_day, to_day)):
        raise MaterialAuditInvalid("INVALID_PERIOD")
    try:
        first, last = date.fromisoformat(from_day), date.fromisoformat(to_day)
    except ValueError:
        raise MaterialAuditInvalid("INVALID_PERIOD") from None
    if not 1 <= (last - first).days + 1 <= 31 or last.year == 9999:
        raise MaterialAuditInvalid("INVALID_PERIOD")
    next_day = last + timedelta(days=1)
    return (
        datetime(first.year, first.month, first.day, tzinfo=zone).astimezone(timezone.utc),
        datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone).astimezone(timezone.utc),
    )


def material_cost_evidence(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                           from_day: str, to_day: str) -> dict:
    if any(type(value) is not UUID for value in (org, loc)):
        raise MaterialAuditInvalid("INVALID_SCOPE")
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                        for_mutation=True):
            raise MaterialAuditDenied("FORBIDDEN")
        # A mixed MANAGER+WAREHOUSE cannot evade a purchase-price DENY by
        # switching roles. This is a branch-wide query: historical cutoffs
        # must deny *all* rows or be applied to each WorkOrder explicitly.
        try:
            for cap in ("READ", "PRICE_PURCHASE"):
                cutoff = require_document_access(
                    conn, token, org, loc, ("MANAGER", "WAREHOUSE"),
                    "SUPPLIER_ORDER", cap)
                if cutoff is not None:
                    raise MaterialAuditDenied("HISTORY_DENIED")
            cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER", "WAREHOUSE"),
                "HANDOVER", "READ")
            if cutoff is not None:
                raise MaterialAuditDenied("HISTORY_DENIED")
        except DocumentPolicyDenied:
            raise MaterialAuditDenied("FORBIDDEN") from None
        branch = conn.execute("""
          SELECT timezone_name FROM auto_service_location
          WHERE id=%s AND organization_id=%s AND disabled_at IS NULL FOR SHARE
        """, (loc, org)).fetchone()
        if branch is None:
            raise MaterialAuditDenied("FORBIDDEN")
        try:
            zone = ZoneInfo(branch[0])
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise MaterialAuditDenied("FORBIDDEN") from None
        start, end = _period(from_day, to_day, zone)

        # One statement gives movements AND current receipt provenance from
        # one PostgreSQL snapshot. Bound raw movements before grouping, so
        # no silent truncation or expensive unlimited aggregation. The
        # work-order/customer proof is revalidated at each GET.
        rows = conn.execute("""
          WITH movements AS MATERIALIZED (
            SELECT id,work_order_id,lot_id,sku_id,quantity,created_at,
                   'ISSUE'::text AS action
            FROM auto_stock_lot_work_order_issue
            WHERE created_at >= %s AND created_at < %s
            UNION ALL
            SELECT id,work_order_id,lot_id,sku_id,quantity,created_at,
                   'RETURN'::text AS action
            FROM auto_stock_lot_work_order_return
            WHERE created_at >= %s AND created_at < %s
          ), scoped AS MATERIALIZED (
            SELECT m.* FROM movements m
            JOIN auto_work_order w ON w.id=m.work_order_id
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
            JOIN auto_stock_lot l ON l.id=m.lot_id
              AND l.organization_id=w.organization_id
              AND l.location_id=w.location_id AND l.sku_id=m.sku_id
            WHERE w.organization_id=%s AND w.location_id=%s
              AND EXISTS (
                SELECT 1 FROM auto_vehicle_customer_link proof
                JOIN auto_vehicle_location_binding binding
                  ON binding.vehicle_id=proof.vehicle_id
                 AND binding.location_id=w.location_id
                WHERE proof.id=a.customer_link_id
                  AND proof.vehicle_id=a.vehicle_id
                  AND proof.verified_at IS NOT NULL
                  AND proof.verified_by IS NOT NULL
                  AND proof.revoked_at IS NULL
              )
          ), bounded AS MATERIALIZED (
            SELECT * FROM scoped ORDER BY created_at,id LIMIT %s
          )
          SELECT m.id,m.work_order_id,m.lot_id,m.sku_id,m.quantity,m.action,
                 m.created_at,
                 receipts.receipt_count,receipts.accepted_count,
                 receipts.valued_count,receipts.quote_currency,
                 receipts.quote_unit_net_minor,receipts.quote_valuation_id
          FROM bounded m
          LEFT JOIN LATERAL (
            SELECT count(*)::bigint AS receipt_count,
                   count(a.id)::bigint AS accepted_count,
                   count(v.id)::bigint AS valued_count,
                   min(v.currency)::text AS quote_currency,
                   min(v.purchase_unit_net_minor)::bigint AS quote_unit_net_minor,
                   min(v.id::text)::uuid AS quote_valuation_id
            FROM auto_stock_receipt_document d
            LEFT JOIN auto_stock_receipt_acceptance a
              ON a.document_id=d.id
            LEFT JOIN auto_stock_receipt_valuation v
              ON v.document_id=d.id AND a.id IS NOT NULL
            WHERE d.lot_id=m.lot_id AND d.sku_id=m.sku_id
              AND d.organization_id=%s AND d.location_id=%s
          ) receipts ON TRUE
          ORDER BY m.created_at,m.id
        """, (start, end, start, end, org, loc, MAX_EVENTS+1, org, loc)).fetchall()
        if len(rows) > MAX_EVENTS:
            raise MaterialAuditLimit("AUDIT_LIMIT")

        entries: dict[tuple[str, str], dict] = {}
        for (event, work_order, lot, sku, quantity, action, occurred,
             receipt_count, accepted_count, valued_count,
             currency, unit, valuation_id) in rows:
            if type(quantity) is not int or not 1 <= quantity <= 1_000_000_000:
                raise MaterialAuditInvalid("CORRUPT_QUANTITY")
            if (receipt_count == 0):
                evidence = "NO_RECEIPT"
            elif (receipt_count == accepted_count == valued_count == 1):
                evidence = "SINGLE_VALUED_RECEIPT"
            else:
                evidence = "REVIEW_REQUIRED"
            key = (str(work_order), str(lot))
            row = entries.setdefault(key, {
                "workOrderId": str(work_order),
                "lotId": str(lot), "skuId": str(sku),
                "grossIssued": 0, "grossReturned": 0,
                "receiptCount": receipt_count,
                "acceptedReceiptCount": accepted_count,
                "valuedReceiptCount": valued_count,
                "quoteEvidence": evidence,
                "quoteValuationId": str(valuation_id) if evidence == "SINGLE_VALUED_RECEIPT" else None,
                "quoteCurrency": currency if evidence == "SINGLE_VALUED_RECEIPT" else None,
                "quoteUnitNetMinor": str(unit) if evidence == "SINGLE_VALUED_RECEIPT" else None,
                "movementIds": [],
            })
            if row["skuId"] != str(sku) or row["quoteEvidence"] != evidence:
                raise MaterialAuditInvalid("INCONSISTENT_LOT")
            row["movementIds"].append(str(event))
            if action == "ISSUE":
                row["grossIssued"] += quantity
            elif action == "RETURN":
                row["grossReturned"] += quantity
            else:
                raise MaterialAuditInvalid("UNKNOWN_ACTION")

        items = sorted(entries.values(), key=lambda x: (x["workOrderId"], x["lotId"]))
        for row in items:
            row["netPeriod"] = row["grossIssued"] - row["grossReturned"]
            row["movementCount"] = len(row["movementIds"])
        kinds = ("NO_RECEIPT", "REVIEW_REQUIRED", "SINGLE_VALUED_RECEIPT")
        summary = {
            "workOrderCount": len({row["workOrderId"] for row in items}),
            "lotCount": len({row["lotId"] for row in items}),
            "movementCount": len(rows),
            "grossIssued": sum(row["grossIssued"] for row in items),
            "grossReturned": sum(row["grossReturned"] for row in items),
            "netPeriod": sum(row["netPeriod"] for row in items),
            "evidenceCounts": {kind: sum(row["quoteEvidence"] == kind for row in items)
                               for kind in kinds},
        }
        return {
            "syntheticOnly": True, "realMoney": False,
            "marginAvailable": False, "releaseAllowed": False,
            "basis": BASIS, "timeZone": branch[0],
            "fromDay": from_day, "toDay": to_day,
            "priceIsOnlyReceiptQuote": True,
            "summary": summary, "items": items,
        }


def _csv(result: dict) -> str:
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Заказ-наряд UUID", "Партия UUID", "SKU UUID",
                     "Выдано", "Возвращено", "Разница", "Документов прихода",
                     "Принято", "Оценено", "Доказательство",
                     "Оценка UUID", "Валюта оценки", "Цена закупки за шт, коп.",
                     "НЕ себестоимость выдачи / НЕ прибыль"))
    for row in result["items"]:
        writer.writerow((row["workOrderId"], row["lotId"], row["skuId"],
                         row["grossIssued"], row["grossReturned"], row["netPeriod"],
                         row["receiptCount"], row["acceptedReceiptCount"],
                         row["valuedReceiptCount"], row["quoteEvidence"],
                         row["quoteValuationId"] or "", row["quoteCurrency"] or "",
                         row["quoteUnitNetMinor"] or "", "QUOTE_ONLY_NOT_COGS"))
    return "\ufeff" + output.getvalue()


def _run(request: Request, org_id: str, loc_id: str, from_day: str, to_day: str):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (TypeError, ValueError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            return material_cost_evidence(
                conn, request.cookies.get("rp_session", ""), org, loc, from_day, to_day)
    except MaterialAuditDenied:
        return _response(request, 403, "FORBIDDEN")
    except MaterialAuditInvalid:
        return _response(request, 422, "INVALID_INPUT")
    except MaterialAuditLimit:
        return _response(request, 409, "AUDIT_LIMIT")


@router.get(PREFIX)
def get_material_cost_evidence(org_id: str, loc_id: str, request: Request,
                               fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    return result if isinstance(result, JSONResponse) else JSONResponse(
        content=result, headers={"Cache-Control": "no-store"})


@router.get(PREFIX + "/export")
def export_material_cost_evidence(org_id: str, loc_id: str, request: Request,
                                  fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    return result if isinstance(result, JSONResponse) else Response(
        content=_csv(result), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition":
                     'attachment; filename="auto-material-cost-evidence.csv"'},
    )
