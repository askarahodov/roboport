"""Immutable synthetic cashbook: account/day/ledger reconciliation, never real money.

The book is a read-only branch-wide financial document. It deliberately
exposes opaque event UUIDs, not WorkOrder IDs, customer data or free-text reasons.
"""
import csv
from datetime import timedelta
from io import StringIO
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_treasury import (
    TreasuryDenied, TreasuryInvalid, TreasuryConflict, _staff, _account, _day,
)
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = ("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
          "/treasury/accounts/{account_id}/cashbook")
MAX_EVENTS = 1000
MAX_BALANCE = 10**15
INCOMING = ("ADVANCE", "PAYMENT")
OUTGOING = ("REFUND_ADVANCE", "REFUND_PAYMENT")
INTERNAL = "APPLY_ADVANCE"
BASIS = "IMMUTABLE_TREASURY_ACCOUNT_CASHBOOK_DAYS"


def cashbook(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
             account_id: UUID, *, from_day: str, to_day: str) -> dict:
    if any(type(value) is not UUID for value in (org, loc, account_id)):
        raise TreasuryInvalid("INVALID_INPUT")
    first, last = _day(from_day), _day(to_day)
    length = (last - first).days + 1
    if not 1 <= length <= 31:
        raise TreasuryInvalid("INVALID_RANGE")
    with conn.transaction():
        # Live MANAGER/CASHIER AND effective FINANCE:READ + PRICE_SALES.
        # Active historical cutoff makes branch-wide amounts forbidden.
        _staff(conn, token, org, loc, ("MANAGER", "CASHIER"))
        # Writers take FOR UPDATE of the account before any new movement or
        # close. Hold SHARE for the *whole* multi-statement reconciliation,
        # not a read-committed series of drifting journal snapshots.
        account = _account(conn, org, loc, account_id)
        opening_raw = conn.execute("""
            SELECT COALESCE(SUM(CASE
                WHEN kind IN ('ADVANCE','PAYMENT') THEN amount_minor
                WHEN kind IN ('REFUND_ADVANCE','REFUND_PAYMENT') THEN -amount_minor
                ELSE 0 END),0)
            FROM auto_treasury_event
            WHERE account_id=%s AND business_day < %s
        """, (account_id, first)).fetchone()[0]
        opening = int(opening_raw)
        if not 0 <= opening <= MAX_BALANCE:
            raise TreasuryConflict("BALANCE_INTEGRITY")
        events = conn.execute("""
            SELECT id, business_day, kind, amount_minor, created_at
            FROM auto_treasury_event
            WHERE account_id=%s AND business_day >= %s AND business_day <= %s
            ORDER BY business_day, created_at, id LIMIT %s
        """, (account_id, first, last, MAX_EVENTS + 1)).fetchall()
        if len(events) > MAX_EVENTS:
            raise TreasuryConflict("CASHBOOK_LIMIT")
        closures = conn.execute("""
            SELECT business_day,id,opening_minor,incoming_minor,
                   outgoing_minor,closing_minor
            FROM auto_treasury_day_close
            WHERE account_id=%s AND business_day >= %s AND business_day <= %s
            ORDER BY business_day
        """, (account_id, first, last)).fetchall()
        by_day = {row[0]: row for row in closures}
        days = []
        index = 0
        total_incoming = total_outgoing = total_internal = 0
        for offset in range(length):
            day = first + timedelta(days=offset)
            start_balance = opening
            incoming = outgoing = internal = 0
            items = []
            while index < len(events) and events[index][1] == day:
                event_id, _, kind, amount, timestamp = events[index]
                if kind in INCOMING:
                    direction = "IN"
                    incoming += amount
                elif kind in OUTGOING:
                    direction = "OUT"
                    outgoing += amount
                elif kind == INTERNAL:
                    direction = "INTERNAL"
                    internal += amount
                else:
                    raise TreasuryConflict("EVENT_INTEGRITY")
                items.append({
                    "eventId": str(event_id), "kind": kind,
                    "amountMinor": str(amount), "cashFlow": direction,
                    "createdAt": timestamp.isoformat(),
                })
                index += 1
            opening = start_balance + incoming - outgoing
            if not 0 <= opening <= MAX_BALANCE:
                raise TreasuryConflict("BALANCE_INTEGRITY")
            closing = by_day.get(day)
            if closing is not None:
                _, close_id, stored_open, stored_in, stored_out, stored_close = closing
                if tuple(map(int, (stored_open, stored_in, stored_out, stored_close))) != (
                    start_balance, incoming, outgoing, opening
                ):
                    raise TreasuryConflict("CLOSE_INTEGRITY")
            else:
                close_id = None
            total_incoming += incoming
            total_outgoing += outgoing
            total_internal += internal
            days.append({
                "localDay": day.isoformat(),
                "openingMinor": str(start_balance),
                "incomingMinor": str(incoming),
                "outgoingMinor": str(outgoing),
                "internalAppliedMinor": str(internal),
                "closingMinor": str(opening),
                "closeId": str(close_id) if close_id is not None else None,
                "events": items,
            })
        return {
            "syntheticOnly": True, "realMoney": False, "releaseAllowed": False,
            "currency": "RUB", "basis": BASIS, "accountId": str(account_id),
            "accountCode": account[1], "accountKind": account[3],
            "fromDay": from_day, "toDay": to_day,
            "summary": {
                "openingMinor": days[0]["openingMinor"],
                "incomingMinor": str(total_incoming),
                "outgoingMinor": str(total_outgoing),
                "internalAppliedMinor": str(total_internal),
                "closingMinor": str(opening),
                "eventCount": len(events),
                "sealedDayCount": len(closures),
            },
            "items": days,
        }


def _csv(result: dict) -> str:
    """Only immutable UUIDs and fixed enums/numbers; no PII or formula input."""
    out = StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(("День", "Счёт UUID", "Событие UUID", "Тип",
                     "Движение", "Сумма, коп.", "Остаток на начало, коп.",
                     "Приход дня, коп.", "Расход дня, коп.",
                     "Зачёт аванса без движения, коп.",
                     "Остаток на конец, коп.", "Закрытие UUID",
                     "Только тестовые средства"))
    for day in result["items"]:
        for event in day["events"] or (None,):
            writer.writerow((
                day["localDay"], result["accountId"],
                event["eventId"] if event else "",
                event["kind"] if event else "",
                event["cashFlow"] if event else "",
                event["amountMinor"] if event else "",
                day["openingMinor"], day["incomingMinor"],
                day["outgoingMinor"], day["internalAppliedMinor"],
                day["closingMinor"], day["closeId"] or "",
                "SIM_ONLY_NOT_REAL_MONEY",
            ))
    return "\ufeff" + out.getvalue()


def _read(request: Request, org_id: str, loc_id: str, account_id: str,
          from_day: str, to_day: str):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc, account = UUID(org_id), UUID(loc_id), UUID(account_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            return cashbook(
                conn, request.cookies.get("rp_session", ""),
                org, loc, account, from_day=from_day, to_day=to_day,
            )
    except TreasuryDenied:
        return _response(request, 403, "FORBIDDEN")
    except TreasuryInvalid:
        return _response(request, 422, "INVALID_INPUT")
    except TreasuryConflict:
        return _response(request, 409, "CASHBOOK_CONFLICT")


@router.get(PREFIX)
def get_cashbook(org_id: str, loc_id: str, account_id: str, request: Request,
                 fromDay: str, toDay: str):
    value = _read(request, org_id, loc_id, account_id, fromDay, toDay)
    return value if isinstance(value, JSONResponse) else JSONResponse(
        content=value, headers={"Cache-Control": "no-store"},
    )


@router.get(PREFIX + "/export")
def export_cashbook(org_id: str, loc_id: str, account_id: str, request: Request,
                    fromDay: str, toDay: str):
    # Always re-authorize and recompute the complete snapshot on export.
    value = _read(request, org_id, loc_id, account_id, fromDay, toDay)
    return value if isinstance(value, JSONResponse) else Response(
        content=_csv(value), media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'attachment; filename="auto-treasury-cashbook.csv"',
        },
    )
