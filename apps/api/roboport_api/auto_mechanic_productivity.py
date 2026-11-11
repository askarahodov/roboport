"""Verified synthetic labor productivity by active mechanic, not payroll.

Only completed technical documents with latest independent QC PASS are included.
All figures are historical recorded minutes: no wages, revenue or PII.
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

from .auto_document_access import DocumentPolicyDenied, require_document_access
from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/reports/mechanic-productivity"
MAX_LABOR_ENTRIES = 500
BASIS = "CLOSED_TECHNICAL_QC_PASSED_ACTIVE_MECHANIC_LABOR"


class ProductivityDenied(ValueError):
    pass


class ProductivityInvalid(ValueError):
    pass


class ProductivityLimit(ValueError):
    pass


def _period(first_raw: str, last_raw: str, zone: ZoneInfo):
    if any(type(day) is not str or re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) is None
           for day in (first_raw, last_raw)):
        raise ProductivityInvalid("INVALID_PERIOD")
    try:
        first, last = date.fromisoformat(first_raw), date.fromisoformat(last_raw)
    except ValueError:
        raise ProductivityInvalid("INVALID_PERIOD") from None
    if not 1 <= (last - first).days + 1 <= 31 or last.year >= 9999:
        raise ProductivityInvalid("INVALID_PERIOD")
    # Local midnight is converted independently on each side: a DST day
    # may be 23 or 25 hours, never assume a fixed 86400-second day.
    after = last + timedelta(days=1)
    start = datetime(first.year, first.month, first.day, tzinfo=zone)
    end = datetime(after.year, after.month, after.day, tzinfo=zone)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def mechanic_productivity(conn: psycopg.Connection, token: str, org: UUID,
                          loc: UUID, from_day: str, to_day: str) -> dict:
    if type(org) is not UUID or type(loc) is not UUID:
        raise ProductivityInvalid("INVALID_SCOPE")
    with conn.transaction():
        if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                        for_mutation=True):
            raise ProductivityDenied("FORBIDDEN")
        try:
            cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER",), "HANDOVER", "READ")
        except DocumentPolicyDenied:
            raise ProductivityDenied("FORBIDDEN") from None
        location = conn.execute("""
            SELECT timezone_name FROM auto_service_location
            WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
            FOR SHARE
        """, (loc, org)).fetchone()
        if location is None:
            raise ProductivityDenied("FORBIDDEN")
        try:
            zone = ZoneInfo(location[0])
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise ProductivityDenied("FORBIDDEN") from None
        start, end = _period(from_day, to_day, zone)

        # A labor event is counted once and only if the sealed technical order
        # has been closed, QC finished independently and the *current* QC is PASS.
        # Revoked vehicle proof and revoked mechanic employment are fail-closed.
        # No cross-ledger money, supplier prices or customer identities.
        rows = conn.execute("""
            SELECT l.actor_id, e.work_order_id, l.service_id, l.actual_minutes
            FROM auto_work_order_execution_finish finish
            JOIN auto_work_order_execution e ON e.id=finish.execution_id
            JOIN auto_work_order w ON w.id=e.work_order_id
              AND w.organization_id=e.organization_id
              AND w.location_id=e.location_id
            JOIN auto_work_order_labor_entry l ON l.execution_id=e.id
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            JOIN auto_vehicle_location_binding binding
              ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
            JOIN app_user staff ON staff.id=l.actor_id AND staff.status='ACTIVE'
            WHERE w.organization_id=%s AND w.location_id=%s
              AND finish.created_at >= %s AND finish.created_at < %s
              AND (%s::timestamptz IS NULL OR w.created_at >= %s::timestamptz)
              AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
              AND proof.revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM auto_organization_role_grant g
                WHERE g.organization_id=w.organization_id AND g.user_id=l.actor_id
                  AND g.role_code='MECHANIC' AND g.revoked_at IS NULL
                  AND (g.location_id=w.location_id OR g.location_id IS NULL)
              )
              AND EXISTS (
                SELECT 1 FROM auto_order_demo_document_event posted
                WHERE posted.work_order_id=w.id AND posted.action='POSTED'
              )
              AND EXISTS (
                SELECT 1 FROM auto_order_demo_document_event closed
                WHERE closed.work_order_id=w.id AND closed.action='CLOSED_TECHNICAL'
              )
              AND EXISTS (
                SELECT 1 FROM auto_work_order_qc_event q
                WHERE q.work_order_id=w.id AND q.result='PASS'
                  AND q.created_at >= finish.created_at
              )
              AND (
                SELECT qc.result FROM auto_work_order_qc_event qc
                WHERE qc.work_order_id=w.id
                ORDER BY qc.created_at DESC,qc.id DESC LIMIT 1
              )='PASS'
            ORDER BY finish.created_at,l.id
            LIMIT %s
        """, (org, loc, start, end, cutoff, cutoff,
              MAX_LABOR_ENTRIES + 1)).fetchall()
        if len(rows) > MAX_LABOR_ENTRIES:
            raise ProductivityLimit("REPORT_LIMIT")

        employees: dict[str, dict] = {}
        all_orders: set[str] = set()
        total_minutes = 0
        for actor, order, service, minutes in rows:
            if type(minutes) is not int or not 1 <= minutes <= 1440:
                raise ProductivityInvalid("CORRUPTED_LABOR")
            person = str(actor)
            order_id = str(order)
            entry = employees.setdefault(person, {
                "mechanicId": person, "workOrderCount": 0,
                "serviceCount": 0, "actualMinutes": 0, "orders": {},
            })
            orders = entry["orders"]
            if order_id not in orders:
                orders[order_id] = {
                    "workOrderId": order_id, "serviceCount": 0,
                    "actualMinutes": 0, "serviceIds": [],
                }
            item = orders[order_id]
            item["serviceCount"] += 1
            item["actualMinutes"] += minutes
            item["serviceIds"].append(str(service))
            entry["serviceCount"] += 1
            entry["actualMinutes"] += minutes
            all_orders.add(order_id)
            total_minutes += minutes

        items = []
        for entry in employees.values():
            orders = entry.pop("orders")
            entry["workOrderCount"] = len(orders)
            entry["orders"] = sorted(orders.values(),
                                     key=lambda row: row["workOrderId"])
            items.append(entry)
        items.sort(key=lambda row: (-row["actualMinutes"], row["mechanicId"]))
        return {
            "syntheticOnly": True, "realMoney": False,
            "payrollAvailable": False, "releaseAllowed": False,
            "basis": BASIS, "timeZone": location[0],
            "fromDay": from_day, "toDay": to_day,
            "activeMechanicsOnly": True, "limitedToEntries": MAX_LABOR_ENTRIES,
            "summary": {
                "mechanicCount": len(items),
                "workOrderCount": len(all_orders),
                "serviceCount": len(rows),
                "actualMinutes": total_minutes,
            },
            "items": items,
        }


def _csv_export(report: dict) -> str:
    """Only UUIDs and integer counts; no executable spreadsheet fields."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Механик UUID", "Заказ-наряд UUID",
                     "Услуг", "Факт, минут",
                     "Статус", "Не является зарплатой"))
    for person in report["items"]:
        for order in person["orders"]:
            writer.writerow((person["mechanicId"], order["workOrderId"],
                             order["serviceCount"], order["actualMinutes"],
                             "CLOSED_TECHNICAL / QC PASS", "DEV / НЕ PAYROLL"))
    return "\ufeff" + output.getvalue()


def _run(request: Request, org_id: str, loc_id: str,
         from_day: str, to_day: str):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            return mechanic_productivity(
                conn, request.cookies.get("rp_session", ""),
                org, loc, from_day, to_day)
    except ProductivityDenied:
        return _response(request, 403, "FORBIDDEN")
    except ProductivityInvalid:
        return _response(request, 422, "INVALID_INPUT")
    except ProductivityLimit:
        return _response(request, 409, "REPORT_LIMIT")


@router.get(PREFIX)
def get_mechanic_productivity(org_id: str, loc_id: str, request: Request,
                              fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get(PREFIX + "/export")
def export_mechanic_productivity(org_id: str, loc_id: str, request: Request,
                                 fromDay: str, toDay: str):
    # Recheck the full live role, document policy and source query on export.
    result = _run(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    return Response(
        content=_csv_export(result), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition":
                     'attachment; filename="auto-mechanic-productivity.csv"'},
    )
