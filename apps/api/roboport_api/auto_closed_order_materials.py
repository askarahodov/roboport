"""Conservative synthetic material valuation of technically closed orders (#448).

Only traceable receipt/issue/return history qualifies, with FIFO for multiple receipts. This is net
receipt-price-based consumption, not accounting COGS, profit or payroll.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from io import StringIO
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_closed_order_report import (
    report as closed_report, Denied, Conflict, MAX_SAFE,
)
from . import auto_material_fifo as fifo
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/reports/closed-order-materials"
BASIS = "CLOSED_TECHNICAL_RECEIPT_FIFO_NET_MATERIAL_USAGE"
MAX_MOVEMENTS = 500
MAX_ALLOCATIONS = 5000


def _source(rows):
    """Every allocation must have exactly one valid immutable provenance leg."""
    receipts = []
    first_issue = None
    for (lot, allocation, action, qty, receipt, acceptance, valuation,
         currency, unit, valuation_seq, receipt_seq, issue, returned,
         provenance_count, valid, allocation_seq) in rows:
        if provenance_count != 1 or not valid or allocation_seq is None:
            return "UNPROVEN", None
        if receipt is not None:
            receipts.append((receipt, acceptance, valuation, currency, unit,
                             valuation_seq, receipt_seq))
        if issue is not None:
            first_issue = allocation_seq if first_issue is None else min(first_issue, allocation_seq)
    if len(receipts) != 1 or first_issue is None:
        return "UNPROVEN", None
    receipt, acceptance, valuation, currency, unit, valuation_seq, receipt_seq = receipts[0]
    if receipt_seq is None or receipt_seq >= first_issue:
        return "UNPROVEN", None
    if valuation is None:
        return "UNVALUED", None
    if valuation_seq is None or valuation_seq <= receipt_seq:
        return "UNPROVEN", None
    if valuation_seq >= first_issue:
        return "LATE_VALUATION", None
    if currency != "RUB":
        return "OTHER_CURRENCY", None
    if type(unit) is not int or not 0 <= unit <= 10**9:
        raise Conflict()
    return "TRACED", {
        "receiptId": str(receipt), "acceptanceId": str(acceptance),
        "valuationId": str(valuation), "unitNetMinor": unit,
    }


def material_report(conn, token, org, loc, from_day, to_day):
    with conn.transaction():
        # Один согласованный снимок для списка закрытых заказов, расхода,
        # приёмок и политик. Конкурентный отзыв может вернуть 409.
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        base = closed_report(conn, token, org, loc, from_day, to_day)
        return _material_report(conn, token, org, loc, from_day, to_day, base)


def _material_report(conn, token, org, loc, from_day, to_day, base):
    # Вызывается только внутри общего REPEATABLE READ snapshot с проверенным base.
    try:
        for cap in ("READ", "PRICE_PURCHASE"):
            if require_document_access(conn, token, org, loc,
                    ("MANAGER", "WAREHOUSE"), "SUPPLIER_ORDER", cap) is not None:
                raise Denied()
    except DocumentPolicyDenied:
        raise Denied() from None
    order_ids = [UUID(row["workOrderId"]) for row in base["items"]]
    movements = conn.execute("""
      SELECT m.work_order_id,m.stock_event_id,m.sku_id,m.action,m.quantity,
             COALESCE(i.lot_id,r.lot_id) AS lot_id,
             COALESCE(i.id,r.id) AS movement_id,
             (e.id IS NOT NULL AND e.sku_id=m.sku_id
              AND e.action=m.action AND e.quantity=m.quantity
              AND e.organization_id=%s AND e.location_id=%s
              AND l.organization_id=%s AND l.location_id=%s
              AND l.sku_id=m.sku_id
              AND ((m.action='ISSUE' AND i.id IS NOT NULL AND r.id IS NULL
                    AND i.work_order_id=m.work_order_id AND i.sku_id=m.sku_id
                    AND i.quantity=m.quantity AND a.action='DEALLOCATE')
                OR (m.action='RETURN' AND r.id IS NOT NULL AND i.id IS NULL
                    AND r.work_order_id=m.work_order_id AND r.sku_id=m.sku_id
                    AND r.quantity=m.quantity AND a.action='ALLOCATE'))
              AND a.lot_id=l.id AND a.sku_id=m.sku_id
              AND a.quantity=m.quantity) AS paired
      FROM auto_work_order_stock_event m
      LEFT JOIN auto_stock_event e ON e.id=m.stock_event_id
      LEFT JOIN auto_stock_lot_work_order_issue i ON i.stock_event_id=m.stock_event_id
      LEFT JOIN auto_stock_lot_work_order_return r ON r.stock_event_id=m.stock_event_id
      LEFT JOIN auto_stock_lot l ON l.id=COALESCE(i.lot_id,r.lot_id)
      LEFT JOIN auto_stock_lot_allocation a ON a.id=COALESCE(i.lot_debit_id,r.lot_credit_id)
      WHERE m.work_order_id=ANY(%s::uuid[]) AND m.action IN ('ISSUE','RETURN')
      ORDER BY m.work_order_id,m.stock_event_id LIMIT %s
    """, (org, loc, org, loc, order_ids, MAX_MOVEMENTS+1)).fetchall()
    if len(movements) > MAX_MOVEMENTS:
        raise Conflict()
    lots = sorted({row[5] for row in movements if row[7] is True})
    history = conn.execute("""
      SELECT a.lot_id,a.id,a.action,a.quantity,d.id,ac.id,v.id,
             v.currency,v.purchase_unit_net_minor,v.cost_registration_seq,
                 CASE WHEN ac.id IS NOT NULL THEN a.cost_registration_seq END,
             i.id,r.id,
             (CASE WHEN ac.id IS NULL THEN 0 ELSE 1 END
              + CASE WHEN i.id IS NULL THEN 0 ELSE 1 END
              + CASE WHEN r.id IS NULL THEN 0 ELSE 1 END) AS legs,
             CASE
               WHEN ac.id IS NOT NULL THEN
                 d.lot_id=a.lot_id AND d.sku_id=a.sku_id
                 AND d.organization_id=l.organization_id AND d.location_id=l.location_id
                 AND d.quantity=a.quantity AND a.action='ALLOCATE'
                 AND se.action='RECEIPT' AND se.quantity=a.quantity
                 AND se.sku_id=a.sku_id AND se.organization_id=l.organization_id
                 AND se.location_id=l.location_id
               WHEN i.id IS NOT NULL THEN
                 i.lot_id=a.lot_id AND i.sku_id=a.sku_id AND i.quantity=a.quantity
                 AND a.action='DEALLOCATE' AND wi.organization_id=l.organization_id
                 AND wi.location_id=l.location_id
                 AND mi.work_order_id=i.work_order_id AND mi.sku_id=a.sku_id
                 AND mi.action='ISSUE' AND mi.quantity=a.quantity
               WHEN r.id IS NOT NULL THEN
                 r.lot_id=a.lot_id AND r.sku_id=a.sku_id AND r.quantity=a.quantity
                 AND a.action='ALLOCATE' AND wr.organization_id=l.organization_id
                 AND wr.location_id=l.location_id
                 AND mr.work_order_id=r.work_order_id AND mr.sku_id=a.sku_id
                 AND mr.action='RETURN' AND mr.quantity=a.quantity
               ELSE false END AS valid, a.cost_registration_seq,
             COALESCE(i.work_order_id,r.work_order_id)
      FROM auto_stock_lot_allocation a
      JOIN auto_stock_lot l ON l.id=a.lot_id AND l.sku_id=a.sku_id
      LEFT JOIN auto_stock_receipt_acceptance ac ON ac.allocation_event_id=a.id
      LEFT JOIN auto_stock_receipt_document d ON d.id=ac.document_id
      LEFT JOIN auto_stock_receipt_valuation v ON v.document_id=d.id
      LEFT JOIN auto_stock_event se ON se.id=ac.stock_event_id
      LEFT JOIN auto_stock_lot_work_order_issue i ON i.lot_debit_id=a.id
      LEFT JOIN auto_work_order wi ON wi.id=i.work_order_id
      LEFT JOIN auto_work_order_stock_event mi ON mi.stock_event_id=i.stock_event_id
      LEFT JOIN auto_stock_lot_work_order_return r ON r.lot_credit_id=a.id
      LEFT JOIN auto_work_order wr ON wr.id=r.work_order_id
      LEFT JOIN auto_work_order_stock_event mr ON mr.stock_event_id=r.stock_event_id
      WHERE a.lot_id=ANY(%s::uuid[]) AND l.organization_id=%s AND l.location_id=%s
      ORDER BY a.lot_id,a.id LIMIT %s
    """, (lots, org, loc, MAX_ALLOCATIONS+1)).fetchall()
    if len(history) > MAX_ALLOCATIONS:
        raise Conflict()
    by_lot = defaultdict(list)
    for row in history:
        by_lot[row[0]].append(row)
    sources = {}
    fifo_results = {}
    # Mixed serial lots lack per-serial receipt attribution.
    serial_lots = {r[0] for r in conn.execute("""
        SELECT DISTINCT lot_id FROM auto_stock_serial WHERE lot_id=ANY(%s::uuid[])
        """,(lots,)).fetchall()}
    slice_budget = fifo.MAX_SLICES
    for lot in lots:
        rows = by_lot[lot]
        if sum(r[4] is not None for r in rows)>1:
            if lot in serial_lots:
                sources[lot]=("UNPROVEN",None)
                continue
            status, parts = fifo.replay(rows,limit=slice_budget)
            slice_budget-=sum(len(p) for p in parts.values())
            sources[lot]=(status,None)
            fifo_results[lot]=parts
        else:
            sources[lot]=_source([r[:16] for r in rows])
    grouped = defaultdict(list)
    for order, event, sku, action, qty, lot, movement, paired in movements:
        if type(qty) is not int or not 1 <= qty <= 10**9:
            raise Conflict()
        # Несопоставленное движение не исчезает из отчёта.
        # Для него нельзя подтвердить стоимость даже по тому же SKU.
        grouped[(order, sku, lot if paired is True else None)].append(
            (event, action, qty, movement if paired is True else None))
    items = []
    complete_total = 0
    complete_count = 0
    all_groups = defaultdict(list)
    for (order, sku, lot), events in grouped.items():
        issued = sum(qty for _, action, qty, _ in events if action == "ISSUE")
        returned = sum(qty for _, action, qty, _ in events if action == "RETURN")
        status, source = sources[lot] if lot is not None else ("UNPAIRED", None)
        net = issued - returned
        if net < 0:
            status, source = "UNPROVEN", None
        cost = net * source["unitNetMinor"] if source else None
        slices = []
        if status == "FIFO_TRACED":
            cost, slices = fifo.group_slices(events, fifo_results[lot])
        if cost is not None and cost > MAX_SAFE:
            raise Conflict()
        all_groups[order].append({
            "skuId": str(sku), "lotId": str(lot) if lot else None,
            "issuedQuantity": issued, "returnedQuantity": returned,
            "netQuantity": net, "status": status, "source": source,
            "materialNetMinor": cost,
            "fifoSlices": slices,
            "movements": [
                {"stockEventId": str(event), "action": action, "quantity": qty,
                 "lotMovementId": str(movement) if movement else None}
                for event, action, qty, movement in events
            ],
        })
    for row in base["items"]:
        groups = sorted(all_groups[UUID(row["workOrderId"])],
                        key=lambda g: (g["skuId"], g["lotId"] or ""))
        complete = all(g["status"] in ("TRACED","FIFO_TRACED") for g in groups)
        cost = sum(g["materialNetMinor"] for g in groups) if complete else None
        if cost is not None:
            complete_count += 1
            complete_total += cost
            if complete_total > MAX_SAFE:
                raise Conflict()
        items.append({
            "workOrderId": row["workOrderId"], "closedDay": row["closedDay"],
            "closedEventId": row["closedEventId"],
            "status": "COMPLETE" if complete else "INCOMPLETE",
            "materialNetMinor": cost, "groups": groups,
        })
    return {
        "syntheticOnly": True, "realMoney": False, "releaseAllowed": False,
        "marginAvailable": False, "payrollAvailable": False, "currency": "RUB",
        "basis": BASIS, "timeZone": base["timeZone"],
        "fromDay": from_day, "toDay": to_day,
        "summary": {"orderCount": len(items), "completeOrderCount": complete_count,
                    "movementCount": len(movements),
                    "materialNetMinor": complete_total if complete_count == len(items) else None},
        "items": items,
    }


def _csv(result):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(("Заказ UUID", "День закрытия", "Закрытие UUID", "Уровень",
                     "Статус", "SKU UUID", "Партия UUID", "Выдано", "Возвращено",
                     "Расход", "Оценка без НДС, коп.", "Приход UUID", "Приёмка UUID",
                     "Оценка UUID", "Цена единицы, коп.", "Складское событие UUID",
                     "Движение партии UUID", "Действие", "Количество", "Исходная выдача UUID"))
    for row in result["items"]:
        order = (row["workOrderId"], row["closedDay"], row["closedEventId"])
        writer.writerow((*order, "ORDER", row["status"], "", "", "", "", "",
                         row["materialNetMinor"], "", "", "", "", "", "", "", "", ""))
        for group in row["groups"]:
            source = group["source"] or {}
            writer.writerow((*order, "LOT", group["status"], group["skuId"], group["lotId"],
                             group["issuedQuantity"], group["returnedQuantity"], group["netQuantity"],
                             group["materialNetMinor"], source.get("receiptId"),
                             source.get("acceptanceId"), source.get("valuationId"),
                             source.get("unitNetMinor"), "", "", "", "", ""))
            for movement in group["movements"]:
                writer.writerow((*order, "MOVEMENT", "", group["skuId"], group["lotId"],
                                 "", "", "", "", "", "", "", "", movement["stockEventId"],
                                 movement["lotMovementId"], movement["action"], movement["quantity"], ""))
            for s in group["fifoSlices"]:
                writer.writerow((*order,"FIFO_SLICE","",group["skuId"],group["lotId"],"","","",
                                 s["quantity"]*s["unitNetMinor"]*(1 if s["action"]=="ISSUE" else -1),
                                 s["receiptId"],s["acceptanceId"],s["valuationId"],s["unitNetMinor"],
                                 s["stockEventId"],s["lotMovementId"],s["action"],s["quantity"],s["originalIssueId"]))
    return "\ufeff" + output.getvalue()


def _run(request, org_id, loc_id, from_day, to_day):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    try:
        with psycopg.connect(dsn) as conn:
            return material_report(conn, request.cookies.get("rp_session", ""),
                                   UUID(org_id), UUID(loc_id), from_day, to_day)
    except Denied:
        return _response(request, 403, "FORBIDDEN")
    except (Conflict, psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected):
        return _response(request, 409, "REPORT_CONFLICT")
    except ValueError:
        return _response(request, 422, "INVALID_INPUT")


@router.get(PREFIX)
def get_materials(org_id: str, loc_id: str, request: Request, fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    return result if isinstance(result, JSONResponse) else JSONResponse(
        content=result, headers={"Cache-Control": "no-store"})


@router.get(PREFIX + "/export")
def export_materials(org_id: str, loc_id: str, request: Request, fromDay: str, toDay: str):
    result = _run(request, org_id, loc_id, fromDay, toDay)
    return result if isinstance(result, JSONResponse) else Response(
        content=_csv(result), media_type="text/csv; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                 "Content-Disposition": 'attachment; filename="auto-closed-order-materials.csv"'})
