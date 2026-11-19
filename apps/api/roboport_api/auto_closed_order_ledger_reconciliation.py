"""Сверка закрытых ЗН: два НЕЗАВИСИМЫХ синтетических расчётных журнала.

Источники: immutable technical document preview (SIM_PAYMENT/SIM_REFUND),
auto_treasury_event (ADVANCE/PAYMENT/APPLY_ADVANCE/REFUND_*), доказанные
материалы и утверждённая внутренняя ставка. Никакой реальной прибыли.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from io import StringIO
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_closed_order_report import report as closed_report, Denied, Conflict, MAX_SAFE
from .auto_internal_order_costs import cost_report
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = ("/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
          "/reports/closed-order-ledger-reconciliation")
BASIS = "CLOSED_TECHNICAL_TWO_SEPARATE_SIMULATED_SETTLEMENT_LEDGERS"
MAX_LEDGER_EVENTS = 500
MAX_CSV_BYTES = 1024 * 1024
_KINDS = ("ADVANCE", "PAYMENT", "APPLY_ADVANCE",
          "REFUND_ADVANCE", "REFUND_PAYMENT")


def _money(value: int) -> str:
    if type(value) is not int or abs(value) > 200 * MAX_SAFE:
        raise Conflict()
    return str(value)


def _sources(cost: dict) -> dict:
    """Прозрачные UUID доказательств; ни сотрудник, ни VIN, ни причины."""
    labor = []
    lots = []
    for entry in cost["laborEntries"]:
        source = entry["source"]
        labor.append({
            "laborEntryId": str(UUID(entry["laborEntryId"])),
            "status": entry["status"],
            "approvalId": str(UUID(source["approvalId"])) if source else None,
            "rateRevisionId": str(UUID(source["revisionId"])) if source else None,
        })
    for group in cost["materialGroups"]:
        source = group["source"]
        lots.append({
            "lotId": str(UUID(group["lotId"])) if group["lotId"] else None,
            "skuId": str(UUID(group["skuId"])),
            "status": group["status"],
            "receiptId": str(UUID(source["receiptId"])) if source and source.get("receiptId") else None,
            "valuationId": str(UUID(source["valuationId"])) if source and source.get("valuationId") else None,
        })
    return {"labor": labor, "lots": lots}


def reconciliation(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                   from_day: str, to_day: str) -> dict:
    if type(org) is not UUID or type(loc) is not UUID:
        raise ValueError("INVALID_SCOPE")
    with conn.transaction():
        # Один снимок для документов, стоимости и ОБОИХ расчётных журналов.
        # Policy writer обновляет MVCC версию org-row: старый RR snapshot
        # после нового FINANCE DENY получает 40001, не старое разрешение.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        costs = cost_report(conn, token, org, loc, from_day, to_day)
        closes = closed_report(conn, token, org, loc, from_day, to_day)
        if (costs["timeZone"] != closes["timeZone"] or
            costs["fromDay"] != closes["fromDay"] or
            costs["toDay"] != closes["toDay"]):
            raise Conflict()
        by_id = {row["workOrderId"]: row for row in closes["items"]}
        if len(by_id) != len(closes["items"]) or len(by_id) != len(costs["items"]):
            raise Conflict()
        ids = [UUID(row["workOrderId"]) for row in costs["items"]]
        # Текущий approved revision из точно проверенного пакета закрытия.
        # Журнал treasury не может незаметно присоединиться к чужой
        # организации/филиалу: источник отбирается по заказу, а scope
        # каждого account/revision проверяется как обязательный инвариант.
        rows = conn.execute("""
            SELECT e.work_order_id,e.id,e.account_id,e.revision_id,
                   e.kind,e.amount_minor,e.business_day,
                   a.organization_id,a.location_id,a.currency
            FROM auto_treasury_event e
            JOIN auto_treasury_account a ON a.id=e.account_id
            WHERE e.work_order_id=ANY(%s::uuid[])
            ORDER BY e.work_order_id,e.created_at,e.id LIMIT %s
        """, (ids, MAX_LEDGER_EVENTS + 1)).fetchall()
        if len(rows) > MAX_LEDGER_EVENTS:
            raise Conflict()
        events = defaultdict(list)
        seen = set()
        for order, event_id, account, revision, kind, amount, day, tenant, branch, currency in rows:
            key = str(order)
            close = by_id.get(key)
            if (close is None or str(revision) != close["estimateRevisionId"]
                or tenant != org or branch != loc or currency != "RUB"
                or kind not in _KINDS or type(amount) is not int
                or not 1 <= amount <= 10**12 or event_id in seen):
                raise Conflict()
            seen.add(event_id)
            events[key].append({
                "eventId": str(event_id), "accountId": str(account),
                "revisionId": str(revision), "kind": kind,
                "amountMinor": str(amount), "businessDay": day.isoformat(),
            })

        items = []
        for cost in costs["items"]:
            key = cost["workOrderId"]
            close = by_id.pop(key, None)
            if close is None or (
                cost["closedEventId"] != close["closedEventId"] or
                cost["previewId"] != close["previewId"] or
                cost["previewDigest"] != close["previewDigest"] or
                cost["approvedTotalMinor"] != close["approvedTotalMinor"] or
                cost["closedDay"] != close["closedDay"]
            ):
                raise Conflict()
            money = cost["approvedTotalMinor"]
            ledger = events[key]
            amounts = {kind: sum(int(e["amountMinor"]) for e in ledger
                                 if e["kind"] == kind) for kind in _KINDS}
            advance = amounts["ADVANCE"] - amounts["APPLY_ADVANCE"] - amounts["REFUND_ADVANCE"]
            settled = amounts["PAYMENT"] + amounts["APPLY_ADVANCE"] - amounts["REFUND_PAYMENT"]
            cash_net = (amounts["ADVANCE"] + amounts["PAYMENT"]
                        - amounts["REFUND_ADVANCE"] - amounts["REFUND_PAYMENT"])
            if (min(advance, settled, cash_net) < 0 or
                settled + advance > money or cash_net != settled + advance or
                any(value > MAX_SAFE for value in (advance, settled, cash_net))):
                raise Conflict()
            # События legacy demo расчёта не являются событиями treasury;
            # ровное совпадение двух сумм НЕ доказывает реальный платёж.
            demo_paid = close["simulatedPaidMinor"]
            demo_refunded = close["simulatedRefundedMinor"]
            demo_net = demo_paid - demo_refunded
            if demo_net != money:
                raise Conflict()
            difference = demo_net - settled
            ledger_status = ("NO_TREASURY_EVENTS" if not ledger
                             else "SETTLED" if settled == money and advance == 0
                             else "PARTIAL")
            items.append({
                "workOrderId": key, "closedDay": cost["closedDay"],
                "closedEventId": cost["closedEventId"],
                "previewId": cost["previewId"],
                "previewDigest": cost["previewDigest"],
                "revisionId": close["estimateRevisionId"],
                "approvedTotalMinor": _money(money),
                "directCostMinor": (_money(cost["directCostMinor"])
                                    if cost["directCostMinor"] is not None else None),
                "syntheticDifferenceMinor": (
                    _money(cost["syntheticDifferenceMinor"])
                    if cost["syntheticDifferenceMinor"] is not None else None),
                "costStatus": cost["status"],
                "demoNetMinor": _money(demo_net),
                "ledgerStatus": ledger_status,
                "treasurySettledMinor": _money(settled),
                "unappliedAdvanceMinor": _money(advance),
                "treasuryCashNetMinor": _money(cash_net),
                "treasuryDueMinor": _money(money - settled),
                "demoMinusTreasuryMinor": _money(difference),
                "treasuryEvents": ledger,
                "costEvidence": _sources(cost),
            })
        if by_id:
            raise Conflict()
        result = {
            "syntheticOnly": True, "realMoney": False,
            "releaseAllowed": False, "marginAvailable": False,
            "payrollAvailable": False, "currency": "RUB",
            "basis": BASIS, "timeZone": costs["timeZone"],
            "fromDay": from_day, "toDay": to_day,
            "summary": {
                "orderCount": len(items), "costCompleteCount": sum(
                    i["costStatus"] == "COMPLETE" for i in items),
                "ledgerLinkedCount": sum(
                    i["ledgerStatus"] != "NO_TREASURY_EVENTS" for i in items),
                "ledgerSettledCount": sum(
                    i["ledgerStatus"] == "SETTLED" for i in items),
                "treasuryEventCount": len(rows),
                **{field: _money(sum(int(i[key]) for i in items))
                   for field, key in (
                       ("approvedTotalMinor", "approvedTotalMinor"),
                       ("demoNetMinor", "demoNetMinor"),
                       ("treasurySettledMinor", "treasurySettledMinor"),
                       ("unappliedAdvanceMinor", "unappliedAdvanceMinor"),
                       ("treasuryCashNetMinor", "treasuryCashNetMinor"),
                       ("treasuryDueMinor", "treasuryDueMinor"),
                       ("demoMinusTreasuryMinor", "demoMinusTreasuryMinor"))},
            },
            "items": items,
        }
        return result


def _csv(data: dict) -> str:
    """Одна финансовая сумма на ORDER; в деталях только UUID/фиксированные enum."""
    out = StringIO(newline="")
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow((
        "Уровень", "Заказ UUID", "День закрытия", "Закрытие UUID",
        "Смета UUID", "Статус затрат", "Статус реестра",
        "Смета, коп.", "Прямые затраты, коп.", "Условная разница, коп.",
        "Имитация расчёта, коп.", "Касса зачтено, коп.",
        "Касса остаток аванса, коп.", "Касса долг, коп.",
        "Разница двух реестров, коп.", "Событие UUID", "Счёт UUID",
        "Вид события", "Сумма события, коп.", "День события",
        "Труд UUID", "Утверждение ставки UUID", "Партия UUID",
        "Оценка UUID", "Только синтетические данные"))
    for row in data["items"]:
        key = str(UUID(row["workOrderId"]))
        revision = str(UUID(row["revisionId"]))
        base = (key, row["closedDay"], str(UUID(row["closedEventId"])), revision)
        writer.writerow((
            "ORDER", *base, row["costStatus"], row["ledgerStatus"],
            row["approvedTotalMinor"], row["directCostMinor"] or "",
            row["syntheticDifferenceMinor"] if row["syntheticDifferenceMinor"] is not None else "",
            row["demoNetMinor"], row["treasurySettledMinor"],
            row["unappliedAdvanceMinor"], row["treasuryDueMinor"],
            row["demoMinusTreasuryMinor"], *[""]*9,
            "SIM_ONLY_NOT_REAL_MONEY"))
        for event in row["treasuryEvents"]:
            if event["kind"] not in _KINDS:
                raise Conflict()
            writer.writerow((
                "TREASURY_EVENT", *base, "", "", *[""]*8,
                str(UUID(event["eventId"])), str(UUID(event["accountId"])),
                event["kind"], event["amountMinor"], event["businessDay"],
                *[""]*4, "SIM_ONLY_NOT_REAL_MONEY"))
        for entry in row["costEvidence"]["labor"]:
            writer.writerow((
                "LABOR_SOURCE", *base, entry["status"], "", *[""]*8,
                *[""]*5, entry["laborEntryId"], entry["approvalId"] or "",
                "", "", "SIM_ONLY_NOT_REAL_MONEY"))
        for lot in row["costEvidence"]["lots"]:
            writer.writerow((
                "MATERIAL_SOURCE", *base, lot["status"], "", *[""]*8,
                *[""]*5, "", "", lot["lotId"] or "",
                lot["valuationId"] or "", "SIM_ONLY_NOT_REAL_MONEY"))
    content = ("\ufeff" + out.getvalue()).encode("utf-8")
    if len(content) > MAX_CSV_BYTES:
        raise Conflict()
    return content.decode("utf-8")


def _run(request: Request, org_id: str, loc_id: str,
         from_day: str, to_day: str, *, export: bool = False):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        org, loc = UUID(org_id), UUID(loc_id)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as db:
            data = reconciliation(db, request.cookies.get("rp_session", ""),
                                  org, loc, from_day, to_day)
        if export:
            return Response(
                content=_csv(data), media_type="text/csv; charset=utf-8",
                headers={
                    "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                    "Content-Disposition":
                        'attachment; filename="auto-closed-ledger-reconciliation.csv"',
                },
            )
        return JSONResponse(content=data, headers={"Cache-Control": "no-store"})
    except Denied:
        return _response(request, 403, "FORBIDDEN")
    except (Conflict, psycopg.errors.SerializationFailure,
            psycopg.errors.DeadlockDetected):
        return _response(request, 409, "RECONCILIATION_CONFLICT")
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")


@router.get(PREFIX)
def get_reconciliation(org_id: str, loc_id: str, request: Request,
                       fromDay: str, toDay: str):
    return _run(request, org_id, loc_id, fromDay, toDay)


@router.get(PREFIX + "/export")
def export_reconciliation(org_id: str, loc_id: str, request: Request,
                          fromDay: str, toDay: str):
    return _run(request, org_id, loc_id, fromDay, toDay, export=True)
