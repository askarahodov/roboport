"""Verified synthetic branch cash flow from immutable treasury account cashbooks (#445).

Read-only, no real payments. Full source UUID lineage for every aggregate.
Each account is reconciled by the existing cashbook invariants under ONE
repeatable-read snapshot with live FINANCE document and role permissions.
"""
from __future__ import annotations

import csv
import re
from datetime import timedelta
from io import StringIO
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_treasury import (
    TreasuryConflict, TreasuryDenied, TreasuryInvalid, _day, _staff,
)
from .auto_treasury_cashbook import cashbook, MAX_BALANCE
from .auto_vehicle_claim_http import _request_dsn, _response


router = APIRouter(include_in_schema=False)
PREFIX = ("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
          "/treasury/cashflow")
BASIS = "VERIFIED_BRANCH_CASHFLOW_FROM_IMMUTABLE_ACCOUNT_CASHBOOKS"
MAX_ACCOUNTS = 50
MAX_EVENTS = 1000
MAX_EXPORT_BYTES = 1024 * 1024
FLOW = ("IN", "OUT", "INTERNAL")
KINDS = ("ADVANCE", "PAYMENT", "REFUND_ADVANCE",
         "REFUND_PAYMENT", "APPLY_ADVANCE")


def branch_cashflow(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                    from_day: str, to_day: str) -> dict:
    if type(org) is not UUID or type(loc) is not UUID:
        raise TreasuryInvalid("INVALID_SCOPE")
    first, last = _day(from_day), _day(to_day)
    length = (last-first).days+1
    if not 1 <= length <= 31:
        raise TreasuryInvalid("INVALID_RANGE")
    with conn.transaction():
        # One coherent snapshot: account enumeration, opening balances, every
        # immutable event and each account's day-close proof. If live grant
        # revocation races a locked read, PostgreSQL 40001 -> HTTP 409.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        _staff(conn, token, org, loc, ("MANAGER", "CASHIER"))
        accounts = conn.execute("""
            SELECT id,code,kind
            FROM auto_treasury_account
            WHERE organization_id=%s AND location_id=%s
            ORDER BY id LIMIT %s FOR SHARE
        """, (org, loc, MAX_ACCOUNTS+1)).fetchall()
        if len(accounts) > MAX_ACCOUNTS:
            raise TreasuryConflict("ACCOUNT_LIMIT")
        ids = [row[0] for row in accounts]
        # A shared account lock prevents authorized writers/closers from
        # slipping in an event after the bounded source selection.
        source_bound = conn.execute("""
            SELECT id FROM auto_treasury_event
            WHERE account_id=ANY(%s::uuid[])
              AND business_day >= %s AND business_day <= %s
            ORDER BY business_day,id LIMIT %s
        """, (ids, first, last, MAX_EVENTS+1)).fetchall()
        if len(source_bound) > MAX_EVENTS:
            raise TreasuryConflict("CASHFLOW_LIMIT")
        days = [{
            "localDay": (first+timedelta(days=offset)).isoformat(),
            "openingMinor": 0,
            "incomingMinor": 0,
            "outgoingMinor": 0,
            "internalAppliedMinor": 0,
            "closingMinor": 0,
            "eventCount": 0,
            "sealedAccountCount": 0,
        } for offset in range(length)]
        result_accounts = []
        seen_sources = set()
        for account_id, code, kind in accounts:
            # Reuse the audited cashbook validator instead of implementing a
            # second accounting algorithm that could diverge on refunds,
            # advances, 23/25-hour days or immutable day-close events.
            book = cashbook(conn, token, org, loc, account_id,
                            from_day=from_day, to_day=to_day)
            if (book["accountId"] != str(account_id)
                or book["accountCode"] != code or book["accountKind"] != kind
                or len(book["items"]) != length):
                raise TreasuryConflict("CASHBOOK_INTEGRITY")
            count = 0
            for day, source in zip(days, book["items"]):
                if source["localDay"] != day["localDay"]:
                    raise TreasuryConflict("CASHBOOK_INTEGRITY")
                for key in ("openingMinor", "incomingMinor", "outgoingMinor",
                            "internalAppliedMinor", "closingMinor"):
                    value = int(source[key])
                    if not 0 <= value <= MAX_BALANCE:
                        raise TreasuryConflict("BALANCE_INTEGRITY")
                    day[key] += value
                if source["closeId"] is not None:
                    day["sealedAccountCount"] += 1
                for event in source["events"]:
                    event_id = event["eventId"]
                    if event_id in seen_sources:
                        raise TreasuryConflict("DUPLICATE_EVENT")
                    seen_sources.add(event_id)
                    count += 1
                day["eventCount"] += len(source["events"])
            if count != book["summary"]["eventCount"]:
                raise TreasuryConflict("CASHBOOK_INTEGRITY")
            result_accounts.append({
                "accountId": book["accountId"], "accountCode": code,
                "accountKind": kind, "summary": book["summary"],
                "days": book["items"],
            })
        if len(seen_sources) != len(source_bound):
            raise TreasuryConflict("SOURCE_MISMATCH")
        # Reconcile each aggregate directly from account-level balances.
        # Opening/closing sums are balances, NOT revenue or income.
        previous = days[0]["openingMinor"]
        for index, day in enumerate(days):
            if day["openingMinor"] != previous:
                raise TreasuryConflict("DAY_CONTINUITY")
            if day["closingMinor"] != (day["openingMinor"]+
                                      day["incomingMinor"]-day["outgoingMinor"]):
                raise TreasuryConflict("DAY_BALANCE")
            previous = day["closingMinor"]
            for key in ("openingMinor", "incomingMinor", "outgoingMinor",
                        "internalAppliedMinor", "closingMinor"):
                if not 0 <= day[key] <= MAX_ACCOUNTS * MAX_BALANCE:
                    raise TreasuryConflict("BRANCH_BALANCE_LIMIT")
        summary = {
            "accountCount": len(accounts),
            "eventCount": len(seen_sources),
            "sealedAccountDayCount": sum(day["sealedAccountCount"] for day in days),
            "openingMinor": str(days[0]["openingMinor"]),
            "incomingMinor": str(sum(d["incomingMinor"] for d in days)),
            "outgoingMinor": str(sum(d["outgoingMinor"] for d in days)),
            "internalAppliedMinor": str(sum(d["internalAppliedMinor"] for d in days)),
            "closingMinor": str(days[-1]["closingMinor"]),
        }
        return {
            "syntheticOnly": True, "realMoney": False, "releaseAllowed": False,
            "currency": "RUB", "basis": BASIS, "fromDay": from_day,
            "toDay": to_day, "maxAccounts": MAX_ACCOUNTS,
            "maxEvents": MAX_EVENTS, "summary": summary,
            "days": [{
                **{k: (str(v) if k.endswith("Minor") else v)
                   for k, v in day.items()}
            } for day in days],
            "accounts": result_accounts,
        }


def _csv_export(data: dict) -> str:
    """Only UUIDs, fixed enums, strict ASCII account codes and numeric totals."""
    out = StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow((
        "Локальный день", "Уровень", "Счёт UUID", "Код счёта", "Тип счёта",
        "Событие UUID", "Тип движения", "Поток", "Сумма, коп.",
        "Остаток на начало, коп.", "Приход, коп.", "Расход, коп.",
        "Зачёт без движения, коп.", "Остаток на конец, коп.",
        "Закрытие дня UUID", "Источник",))
    for day in data["days"]:
        writer.writerow((day["localDay"], "BRANCH", "", "", "", "", "",
                         "", "", day["openingMinor"], day["incomingMinor"],
                         day["outgoingMinor"], day["internalAppliedMinor"],
                         day["closingMinor"], "", "SIM_ONLY_NOT_REAL_MONEY"))
    for account in data["accounts"]:
        code = account["accountCode"]
        kind = account["accountKind"]
        if not isinstance(code, str) or not re.fullmatch(r"[A-Z0-9_-]{2,32}", code):
            raise TreasuryConflict("CSV_INTEGRITY")
        if kind not in ("CASH_SIM", "BANK_SIM"):
            raise TreasuryConflict("CSV_INTEGRITY")
        # ASCII account codes may begin with '-' under the existing DB
        # constraint. Prefix that otherwise formula-like cell with an
        # apostrophe so spreadsheet software treats the code as text.
        csv_code = ("'" + code) if code.startswith(("-", "+", "=", "@")) else code
        account_id = str(UUID(account["accountId"]))
        for day in account["days"]:
            close_id = str(UUID(day["closeId"])) if day["closeId"] else ""
            base = (day["localDay"], account_id, code, kind)
            for event in day["events"] or (None,):
                if event is not None:
                    if event["kind"] not in KINDS or event["cashFlow"] not in FLOW:
                        raise TreasuryConflict("CSV_INTEGRITY")
                    event_id = str(UUID(event["eventId"]))
                else:
                    event_id = ""
                writer.writerow((
                    base[0], "ACCOUNT_EVENT" if event else "ACCOUNT_DAY",
                    base[1], csv_code, base[3],
                    event_id, event["kind"] if event else "",
                    event["cashFlow"] if event else "",
                    event["amountMinor"] if event else "",
                    day["openingMinor"], day["incomingMinor"],
                    day["outgoingMinor"], day["internalAppliedMinor"],
                    day["closingMinor"], close_id, "SIM_ONLY_NOT_REAL_MONEY",
                ))
    csv_bytes = ("\ufeff"+out.getvalue()).encode("utf-8")
    if len(csv_bytes) > MAX_EXPORT_BYTES:
        raise TreasuryConflict("EXPORT_LIMIT")
    return csv_bytes.decode("utf-8")


def _read(request: Request, org_id: str, loc_id: str,
          first: str, last: str) -> dict | JSONResponse:
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (TypeError, ValueError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            return branch_cashflow(conn, request.cookies.get("rp_session", ""),
                                   org, loc, first, last)
    except TreasuryDenied:
        return _response(request, 403, "FORBIDDEN")
    except TreasuryInvalid:
        return _response(request, 422, "INVALID_INPUT")
    except TreasuryConflict:
        return _response(request, 409, "CASHFLOW_CONFLICT")
    except (psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
        return _response(request, 409, "REPORT_CONFLICT")


@router.get(PREFIX)
def get_cashflow(org_id: str, loc_id: str, request: Request,
                 fromDay: str, toDay: str):
    result = _read(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    return JSONResponse(content=result, headers={"Cache-Control": "no-store"})


@router.get(PREFIX + "/export")
def export_cashflow(org_id: str, loc_id: str, request: Request,
                    fromDay: str, toDay: str):
    # Re-authorize and recompute both books and all source events on download.
    result = _read(request, org_id, loc_id, fromDay, toDay)
    if isinstance(result, JSONResponse):
        return result
    try:
        content = _csv_export(result)
    except (TreasuryConflict, ValueError):
        return _response(request, 409, "CASHFLOW_CONFLICT")
    return Response(
        content=content, media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition":
                     'attachment; filename="auto-branch-cashflow.csv"'},
    )
