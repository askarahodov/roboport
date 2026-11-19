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
MAX_SHIFT_ROWS = 500
BASIS = "CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES"


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


def _shift_comparison(conn: psycopg.Connection, org: UUID, loc: UUID,
                      start: datetime, end: datetime, labor: list[dict]) -> dict:
    """Compare active scheduled shift seconds with QC-closed labor minutes.

    This is NOT attendance, pay or worker efficiency. Dates represent the
    shift window and separately the technical closure date. No names, VIN
    or customer information leave the server. A bound of 500 prevents silent
    truncation and exploding per-user source lists.
    """
    rows = conn.execute("""
        SELECT sh.id, sh.user_id, sh.starts_at, sh.ends_at
        FROM auto_staff_shift sh
        JOIN app_user staff ON staff.id=sh.user_id AND staff.status='ACTIVE'
        JOIN LATERAL (
            SELECT g.id FROM auto_organization_role_grant g
            WHERE g.organization_id=sh.organization_id AND g.user_id=sh.user_id
              AND g.role_code='MECHANIC' AND g.revoked_at IS NULL
              AND (g.location_id=sh.location_id OR g.location_id IS NULL)
            ORDER BY g.id LIMIT 1 FOR SHARE OF g
        ) active_mechanic ON TRUE
        WHERE sh.organization_id=%s AND sh.location_id=%s
          AND sh.canceled_at IS NULL
          AND sh.starts_at < %s AND sh.ends_at > %s
        ORDER BY sh.user_id,sh.starts_at,sh.id
        LIMIT %s
        FOR SHARE OF sh,staff
    """, (org,loc,end,start,MAX_SHIFT_ROWS+1)).fetchall()
    if len(rows)>MAX_SHIFT_ROWS:
        raise ProductivityLimit("REPORT_LIMIT")
    by_employee: dict[str,dict] = {
        x["mechanicId"]: {
            "mechanicId":x["mechanicId"], "recordedMinutes":x["actualMinutes"],
            "scheduledSeconds":0, "shifts":[],
        } for x in labor
    }
    for shift_id,actor,first,last in rows:
        seconds = int((min(last,end)-max(first,start)).total_seconds())
        if seconds<=0:
            raise ProductivityInvalid("SHIFT_INTEGRITY")
        person=str(actor)
        row=by_employee.setdefault(person,{
            "mechanicId":person,"recordedMinutes":0,
            "scheduledSeconds":0,"shifts":[],
        })
        row["scheduledSeconds"]+=seconds
        row["shifts"].append({
            "shiftId":str(shift_id),"startsAt":first.isoformat(),
            "endsAt":last.isoformat(),"overlapSeconds":seconds,
        })
    items=[]
    for row in by_employee.values():
        seconds=row["scheduledSeconds"]
        minutes=row["recordedMinutes"]
        row["shiftCount"]=len(row["shifts"])
        row["recordedToScheduledBasisPoints"]=(
            (minutes*60*10000+seconds//2)//seconds if seconds else None)
        row["status"]=("NO_ACTIVE_SHIFT" if not seconds else
                        "NO_CLOSED_LABOR" if not minutes else "COMPARABLE")
        items.append(row)
    items.sort(key=lambda x:x["mechanicId"])
    return {
        "basis":"ACTIVE_SHIFTS_VS_CLOSED_TECHNICAL_LABOR",
        "isAttendance":False,"isPayroll":False,"isEfficiencyScore":False,
        "limitedToShifts":MAX_SHIFT_ROWS,
        "summary":{
            "mechanicCount":len(items),
            "shiftedMechanicCount":sum(bool(x["shiftCount"]) for x in items),
            "shiftCount":len(rows),
            "scheduledSeconds":sum(x["scheduledSeconds"] for x in items),
            "recordedMinutes":sum(x["recordedMinutes"] for x in items),
        },
        "items":items,
    }


def mechanic_productivity(conn: psycopg.Connection, token: str, org: UUID,
                          loc: UUID, from_day: str, to_day: str,
                          *, with_shifts: bool = False) -> dict:
    if type(org) is not UUID or type(loc) is not UUID:
        raise ProductivityInvalid("INVALID_SCOPE")
    with conn.transaction():
        # All source proof and effective permissions are read from ONE snapshot.
        # Concurrent revocation of a locked grant/proof causes 40001 rather
        # than a report built from an obsolete REPEATABLE READ version.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                        for_mutation=True):
            raise ProductivityDenied("FORBIDDEN")
        try:
            cutoff = require_document_access(
                conn, token, org, loc, ("MANAGER", "WAREHOUSE"), "HANDOVER", "READ")
        except DocumentPolicyDenied:
            raise ProductivityDenied("FORBIDDEN") from None
        # A historical cutoff cannot safely produce a branch-wide shift
        # comparison. Reject instead of reporting a partial staffing total.
        if with_shifts and cutoff is not None:
            raise ProductivityDenied("HISTORY_RESTRICTED")
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

        # The reporting day is the immutable CLOSED_TECHNICAL event day:
        # a finish followed by close in another local day is counted only
        # on the later day. No unfinished/unsigned technical order enters.
        # Price, payroll, personal names and VIN are never selected.
        rows = conn.execute("""
            SELECT l.actor_id,e.work_order_id,l.service_id,l.actual_minutes,
                   l.id,finish.id,closed.id,closed.created_at,qc.id
            FROM auto_work_order_execution_finish finish
            JOIN auto_work_order_execution e ON e.id=finish.execution_id
            JOIN auto_work_order w ON w.id=e.work_order_id
              AND w.organization_id=e.organization_id
              AND w.location_id=e.location_id
            JOIN auto_work_order_labor_entry l ON l.execution_id=e.id
            JOIN auto_order_demo_document_event closed
              ON closed.work_order_id=w.id AND closed.action='CLOSED_TECHNICAL'
            JOIN auto_appointment a ON a.id=w.appointment_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            JOIN auto_vehicle_location_binding binding
              ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
            JOIN app_user staff ON staff.id=l.actor_id AND staff.status='ACTIVE'
            JOIN LATERAL (
                SELECT g.id FROM auto_organization_role_grant g
                WHERE g.organization_id=w.organization_id AND g.user_id=l.actor_id
                  AND g.role_code='MECHANIC' AND g.revoked_at IS NULL
                  AND (g.location_id=w.location_id OR g.location_id IS NULL)
                ORDER BY g.id LIMIT 1 FOR SHARE OF g
            ) active_mechanic ON TRUE
            JOIN LATERAL (
                SELECT latest.id,latest.result,latest.created_at
                FROM auto_work_order_qc_event latest
                WHERE latest.work_order_id=w.id
                ORDER BY latest.created_at DESC,latest.id DESC LIMIT 1
            ) qc ON qc.result='PASS' AND qc.created_at >= finish.created_at
                 AND qc.created_at <= closed.created_at
            WHERE w.organization_id=%s AND w.location_id=%s
              AND closed.created_at >= %s AND closed.created_at < %s
              AND (%s::timestamptz IS NULL OR w.created_at >= %s::timestamptz)
              AND l.created_at<=finish.created_at
              AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
              AND proof.revoked_at IS NULL
              AND EXISTS (
                SELECT 1 FROM auto_order_demo_document_event posted
                WHERE posted.work_order_id=w.id AND posted.action='POSTED'
                  AND posted.created_at<=closed.created_at
              )
              AND EXISTS (
                SELECT 1 FROM auto_order_demo_document_event ack
                WHERE ack.work_order_id=w.id AND ack.action='CLIENT_ACK'
                  AND ack.created_at<=closed.created_at
              )
            ORDER BY closed.created_at,l.id
            LIMIT %s
            FOR SHARE OF proof,binding,staff
        """, (org, loc, start, end, cutoff, cutoff,
              MAX_LABOR_ENTRIES + 1)).fetchall()
        if len(rows) > MAX_LABOR_ENTRIES:
            raise ProductivityLimit("REPORT_LIMIT")

        employees: dict[str, dict] = {}
        all_orders: set[str] = set()
        total_minutes = 0
        seen_labor: set[str] = set()
        for (actor,order,service,minutes,labor_id,finish_id,closed_id,
             closed_at,qc_id) in rows:
            if type(minutes) is not int or not 1 <= minutes <= 1440:
                raise ProductivityInvalid("CORRUPTED_LABOR")
            entry_id = str(labor_id)
            if entry_id in seen_labor:
                raise ProductivityInvalid("DUPLICATE_LABOR")
            seen_labor.add(entry_id)
            person,order_id = str(actor),str(order)
            employee = employees.setdefault(person, {
                "mechanicId":person,"workOrderCount":0,
                "serviceCount":0,"actualMinutes":0,"orders":{},
            })
            orders = employee["orders"]
            if order_id not in orders:
                orders[order_id] = {
                    "workOrderId":order_id,
                    "closedDay":closed_at.astimezone(zone).date().isoformat(),
                    "closedEventId":str(closed_id),"finishEventId":str(finish_id),
                    "qcEventId":str(qc_id),
                    "serviceCount":0,"actualMinutes":0,
                    "serviceIds":[],"laborEntries":[],
                }
            item = orders[order_id]
            item["serviceCount"] += 1
            item["actualMinutes"] += minutes
            item["serviceIds"].append(str(service))
            item["laborEntries"].append({
                "laborEntryId":entry_id,"serviceId":str(service),
                "actualMinutes":minutes,
            })
            employee["serviceCount"] += 1
            employee["actualMinutes"] += minutes
            all_orders.add(order_id)
            total_minutes += minutes

        items = []
        for employee in employees.values():
            orders = employee.pop("orders")
            employee["workOrderCount"] = len(orders)
            employee["orders"] = sorted(orders.values(),
                                        key=lambda row:row["workOrderId"])
            for order in employee["orders"]:
                order["laborEntries"].sort(key=lambda row:row["laborEntryId"])
                order["serviceIds"] = [row["serviceId"] for row in order["laborEntries"]]
            items.append(employee)
        items.sort(key=lambda row:(-row["actualMinutes"],row["mechanicId"]))
        result = {
            "syntheticOnly":True,"realMoney":False,
            "payrollAvailable":False,"releaseAllowed":False,
            "basis":BASIS,"periodBasis":"CLOSED_TECHNICAL_LOCAL_DAY",
            "timeZone":location[0],"fromDay":from_day,"toDay":to_day,
            "activeMechanicsOnly":True,"limitedToEntries":MAX_LABOR_ENTRIES,
            "summary":{
                "mechanicCount":len(items),
                "workOrderCount":len(all_orders),
                "serviceCount":len(rows),
                "actualMinutes":total_minutes,
            },
            "items":items,
        }
        if with_shifts:
            result["shiftComparison"]=_shift_comparison(
                conn,org,loc,start,end,items)
        return result


def _csv_export(report: dict) -> str:
    """Immutable source UUIDs, numeric quantities and fixed safe enums only."""
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Механик UUID","Заказ-наряд UUID","День закрытия",
                     "Уровень","Закрытие UUID","Завершение UUID","QC PASS UUID",
                     "Запись труда UUID","Услуга UUID","Услуг","Факт, минут",
                     "Не является зарплатой"))
    for person in report["items"]:
        for order in person["orders"]:
            source=(person["mechanicId"],order["workOrderId"],order["closedDay"])
            refs=(order["closedEventId"],order["finishEventId"],order["qcEventId"])
            writer.writerow((*source,"ORDER",*refs,"","",
                             order["serviceCount"],order["actualMinutes"],"DEV / НЕ PAYROLL"))
            for entry in order["laborEntries"]:
                writer.writerow((*source,"LABOR",*refs,entry["laborEntryId"],
                                 entry["serviceId"],"",entry["actualMinutes"],"DEV / НЕ PAYROLL"))
    return "\ufeff" + output.getvalue()


def _run(request: Request, org_id: str, loc_id: str,
         from_day: str, to_day: str, *, with_shifts: bool = False):
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
                org, loc, from_day, to_day, with_shifts=with_shifts)
    except ProductivityDenied:
        return _response(request, 403, "FORBIDDEN")
    except ProductivityInvalid:
        return _response(request, 422, "INVALID_INPUT")
    except ProductivityLimit:
        return _response(request, 409, "REPORT_LIMIT")
    except (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
        # A revoked grant/proof or concurrent DB update is a retryable
        # snapshot conflict, not an oversized report. Never return partial data.
        return _response(request, 409, "REPORT_CONFLICT")


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


def _csv_shift_comparison(report: dict) -> str:
    """Machine-readable source IDs, safe enums and nonnegative numeric fields."""
    output=StringIO(newline="")
    writer=csv.writer(output,delimiter=";",lineterminator="\r\n")
    writer.writerow(("Механик UUID","Уровень","Смена UUID","Начало смены UTC/offset",
                     "Конец смены UTC/offset","Пересечение, секунд",
                     "Всего смен, секунд","Труд по техзакрытиям, минут",
                     "Отношение, базисные пункты","Ограничение"))
    for item in report["shiftComparison"]["items"]:
        summary=(item["mechanicId"],"MECHANIC","","","", "",
                 item["scheduledSeconds"],item["recordedMinutes"],
                 item["recordedToScheduledBasisPoints"] if item["recordedToScheduledBasisPoints"] is not None else "",
                 item["status"])
        writer.writerow(summary)
        for shift in item["shifts"]:
            writer.writerow((item["mechanicId"],"SHIFT",shift["shiftId"],
                             shift["startsAt"],shift["endsAt"],
                             shift["overlapSeconds"],"","","","SYNTHETIC_NOT_PAYROLL"))
    return "\ufeff"+output.getvalue()


@router.get(PREFIX + "/shift-comparison")
def get_shift_comparison(org_id: str, loc_id: str, request: Request,
                         fromDay: str, toDay: str):
    # Same one-snapshot verified-labor and active-shift read, with strict
    # effective MANAGER scope and no historical cutoff.
    result=_run(request,org_id,loc_id,fromDay,toDay,with_shifts=True)
    if isinstance(result,JSONResponse):
        return result
    return JSONResponse(content=result,headers={"Cache-Control":"no-store"})


@router.get(PREFIX + "/shift-comparison/export")
def export_shift_comparison(org_id: str, loc_id: str, request: Request,
                            fromDay: str, toDay: str):
    # Full reauthorization on each export; no client-supplied totals.
    result=_run(request,org_id,loc_id,fromDay,toDay,with_shifts=True)
    if isinstance(result,JSONResponse):
        return result
    return Response(
        content=_csv_shift_comparison(result), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                 "Content-Disposition":
                     'attachment; filename="auto-mechanic-shift-comparison.csv"'},
    )
