"""Private synthetic contribution bridge for QC-closed, posted work orders (#448).

Every published total is traceable to APPROVED revision/technical closure,
independent QC, synthetic settlement, FIFO receipt valuation and approved
internal labor costs. A missing cost rejects the *difference*, not the order.
This is NEVER real revenue, accounting margin, payroll, or a vehicle release.
"""
from __future__ import annotations
import csv
import re
from datetime import date, timedelta
from io import StringIO
from uuid import UUID

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auto_closed_order_report import Denied, Conflict, MAX_SAFE
from .auto_internal_order_costs import cost_report
from .auto_vehicle_claim_http import _request_dsn, _response

router = APIRouter(include_in_schema=False)
PREFIX = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/reports/closed-contribution"
BASIS = "CLOSED_TECHNICAL_QC_POSTED_SIM_SETTLEMENT_AND_PROVEN_DIRECT_COSTS"
MAX_CSV_BYTES = 1024 * 1024


def _safe(value, *, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int or abs(value) > MAX_SAFE:
        raise Conflict()
    return value


def report(conn, token, org, loc, from_day, to_day):
    if type(org) is not UUID or type(loc) is not UUID:
        raise ValueError("INVALID_SCOPE")
    # Both components are retrieved in the SAME transaction, not a second
    # autocommit report. The cost domain owns its REPEATABLE READ and tenant,
    # document-policy, ownership, staff/revocation, QC and provenance guards.
    costs, closed = cost_report(conn, token, org, loc, from_day, to_day,
                                include_base=True)
    if (costs["fromDay"] != from_day or costs["toDay"] != to_day
        or closed["fromDay"] != from_day or closed["toDay"] != to_day
        or costs["timeZone"] != closed["timeZone"]
        or not all(x.get("realMoney") is False and
                   x.get("releaseAllowed") is False and
                   x.get("marginAvailable") is False
                   for x in (costs, closed))):
        raise Conflict()
    try:
        first, last = date.fromisoformat(from_day), date.fromisoformat(to_day)
    except ValueError:
        raise ValueError("INVALID_PERIOD") from None
    count = (last-first).days+1
    if not 1 <= count <= 31:
        raise ValueError("INVALID_PERIOD")
    closures = {x["workOrderId"]: x for x in closed["items"]}
    if len(closures) != len(closed["items"]) or len(costs["items"]) != len(closures):
        raise Conflict()
    days = [{
        "localDay": (first+timedelta(days=i)).isoformat(),
        "orderCount": 0, "completeOrderCount": 0,
        "incompleteOrderCount": 0, "simCashEventCount": 0,
        "approvedTotalMinor": 0, "simulatedPaidMinor": 0,
        "simulatedRefundedMinor": 0, "simulatedNetMinor": 0,
        "materialNetMinor": 0, "laborCostMinor": 0,
        "directCostMinor": 0, "syntheticDifferenceMinor": 0,
    } for i in range(count)]
    indexed = {day["localDay"]: day for day in days}
    result_items = []
    seen_events = set()
    seen_labor = set()
    seen_movements = set()
    for cost in costs["items"]:
        ident = cost["workOrderId"]
        closed_row = closures.pop(ident, None)
        if closed_row is None or cost["closedEventId"] != closed_row["closedEventId"] or (
            cost["closedDay"] != closed_row["closedDay"] or
            cost["previewId"] != closed_row["previewId"] or
            cost["previewDigest"] != closed_row["previewDigest"] or
            cost["approvedTotalMinor"] != closed_row["approvedTotalMinor"]
        ):
            raise Conflict()
        bucket = indexed.get(cost["closedDay"])
        if bucket is None:
            raise Conflict()
        cash = closed_row["cashEvents"]
        if len(cash) == 0:
            raise Conflict()
        for event in cash:
            event_id = event["id"]
            if event_id in seen_events:
                raise Conflict()
            seen_events.add(event_id)
        for entry in cost["laborEntries"]:
            event_id = entry["laborEntryId"]
            if event_id in seen_labor:
                raise Conflict()
            seen_labor.add(event_id)
        for group in cost["materialGroups"]:
            for movement in group["movements"]:
                event_id = movement["stockEventId"]
                if event_id in seen_movements:
                    raise Conflict()
                seen_movements.add(event_id)
        approved = _safe(cost["approvedTotalMinor"])
        paid = _safe(closed_row["simulatedPaidMinor"])
        refunded = _safe(closed_row["simulatedRefundedMinor"])
        simulated_net = _safe(paid-refunded)
        if simulated_net != approved or closed_row["approvedTotalMinor"] != approved:
            raise Conflict()
        complete = (cost["status"] == "COMPLETE")
        material = _safe(cost["materialNetMinor"],nullable=True)
        labor = _safe(cost["laborCostMinor"],nullable=True)
        direct = _safe(cost["directCostMinor"],nullable=True)
        difference = _safe(cost["syntheticDifferenceMinor"],nullable=True)
        if complete != all(v is not None for v in (material,labor,direct,difference)):
            raise Conflict()
        if complete and (material+labor != direct or approved-direct != difference):
            raise Conflict()
        for field, value in (
            ("approvedTotalMinor",approved),("simulatedPaidMinor",paid),
            ("simulatedRefundedMinor",refunded),("simulatedNetMinor",simulated_net)
        ):
            bucket[field] = _safe(bucket[field]+value)
        bucket["simCashEventCount"] += len(cash)
        bucket["orderCount"] += 1
        bucket["completeOrderCount" if complete else "incompleteOrderCount"] += 1
        if complete:
            for field,value in (
                ("materialNetMinor",material),("laborCostMinor",labor),
                ("directCostMinor",direct),("syntheticDifferenceMinor",difference)
            ):
                if bucket[field] is not None:
                    bucket[field] = _safe(bucket[field]+value)
        else:
            for field in ("materialNetMinor","laborCostMinor","directCostMinor",
                          "syntheticDifferenceMinor"):
                bucket[field] = None
        result_items.append({
            "workOrderId": ident, "closedDay": cost["closedDay"],
            "closedEventId": cost["closedEventId"],
            "postedEventId": closed_row["postedEventId"],
            "ackEventId": closed_row["ackEventId"],
            "previewId": cost["previewId"],
            "previewDigest": cost["previewDigest"],
            "estimateRevisionId": closed_row["estimateRevisionId"],
            "approvedTotalMinor": approved, "simulatedPaidMinor": paid,
            "simulatedRefundedMinor": refunded, "simulatedNetMinor": simulated_net,
            "materialNetMinor": material, "laborCostMinor": labor,
            "directCostMinor": direct, "syntheticDifferenceMinor": difference,
            "status": cost["status"], "cashEvents": cash,
            "laborEntries": cost["laborEntries"],
            "materialGroups": cost["materialGroups"],
        })
    if closures:
        raise Conflict()
    if len(seen_events) != closed["summary"]["cashEventCount"]:
        raise Conflict()
    if len(seen_labor) != costs["summary"]["laborEntryCount"]:
        raise Conflict()
    keys=("approvedTotalMinor","simulatedPaidMinor","simulatedRefundedMinor",
          "simulatedNetMinor","materialNetMinor","laborCostMinor",
          "directCostMinor","syntheticDifferenceMinor")
    summary = {
        "orderCount": len(result_items),
        "completeOrderCount": sum(d["completeOrderCount"] for d in days),
        "incompleteOrderCount": sum(d["incompleteOrderCount"] for d in days),
        "simCashEventCount": len(seen_events),
        "laborEntryCount": len(seen_labor),
        "stockEventCount": len(seen_movements),
        **{key: (
            _safe(sum(d[key] for d in days)) if
            all(d[key] is not None for d in days) else None
        ) for key in keys},
    }
    if (summary["orderCount"] != sum(d["orderCount"] for d in days)
        or summary["orderCount"] != costs["summary"]["orderCount"]
        or summary["completeOrderCount"] != costs["summary"]["completeOrderCount"]
        or summary["approvedTotalMinor"] != closed["summary"]["approvedTotalMinor"]
        or summary["simulatedPaidMinor"] != closed["summary"]["simulatedPaidMinor"]
        or summary["simulatedRefundedMinor"] != closed["summary"]["simulatedRefundedMinor"]):
        raise Conflict()
    return {
        "syntheticOnly": True, "realMoney": False, "releaseAllowed": False,
        "marginAvailable": False, "payrollAvailable": False,
        "currency": "RUB", "basis": BASIS, "timeZone": costs["timeZone"],
        "fromDay": from_day, "toDay": to_day,
        "summary": summary, "days": days, "items": result_items,
    }


def _csv(data):
    """Fixed identifiers, statuses and numbers only; never names or comments."""
    out = StringIO(newline="")
    writer = csv.writer(out,delimiter=";",lineterminator="\r\n")
    writer.writerow(("День","Уровень","Заказ UUID","Источник UUID",
                     "Статус","Согласовано коп.","Оплачено SIM коп.",
                     "Возврат SIM коп.","Материалы коп.","Труд коп.",
                     "Прямые затраты коп.","Разница SIM коп.",
                     "Тип источника","Количество","Источник версии UUID",
                     "Только синтетические данные"))
    def write(day,level,order,event,status,approved="",paid="",refund="",
              material="",labor="",direct="",diff="",source="",qty="",revision=""):
        writer.writerow((day,level,order,event,status,approved,paid,refund,
                         material,labor,direct,diff,source,qty,revision,
                         "SIM_ONLY_NOT_REAL_PROFIT"))
    for day in data["days"]:
        write(day["localDay"],"DAY","","","COMPLETE" if
              day["incompleteOrderCount"]==0 else "INCOMPLETE",
              day["approvedTotalMinor"],day["simulatedPaidMinor"],
              day["simulatedRefundedMinor"],day["materialNetMinor"],
              day["laborCostMinor"],day["directCostMinor"],
              day["syntheticDifferenceMinor"])
    for row in data["items"]:
        write(row["closedDay"],"ORDER",row["workOrderId"],row["closedEventId"],
              row["status"],row["approvedTotalMinor"],row["simulatedPaidMinor"],
              row["simulatedRefundedMinor"],row["materialNetMinor"],
              row["laborCostMinor"],row["directCostMinor"],
              row["syntheticDifferenceMinor"],"CLOSED_TECHNICAL","",
              row["estimateRevisionId"])
        for e in row["cashEvents"]:
            if e["kind"] not in ("SIM_PAYMENT","SIM_REFUND"):
                raise Conflict()
            write(row["closedDay"],"SIM_CASH",row["workOrderId"],e["id"],
                  "SIM_ONLY",source=e["kind"],qty=e["amountMinor"],
                  revision=e["revisionId"])
        for e in row["laborEntries"]:
            if e["status"] not in ("TRACED","NO_APPROVED_RATE","INACTIVE_MECHANIC"):
                raise Conflict()
            write(row["closedDay"],"LABOR",row["workOrderId"],e["laborEntryId"],
                  e["status"],labor=e["costMinor"],source="QC_LABOR_MINUTES",
                  qty=e["actualMinutes"],
                  revision=e["source"]["approvalId"] if e["source"] else "")
        for g in row["materialGroups"]:
            # Net cost belongs to a MATERIAL GROUP, not to each ISSUE/RETURN:
            # repeating the group sum on stock rows would misstate a
            # receipt 2 units / return 1 unit as two full group costs.
            if (not re.fullmatch(r"[A-Z_]{2,40}",g["status"])
                or type(g["netQuantity"]) is not int
                or type(g["materialNetMinor"]) not in (int,type(None))):
                raise Conflict()
            group_id = str(UUID(g["lotId"] or g["skuId"]))
            valuation = str(UUID(g["source"]["valuationId"])) if g["source"] else ""
            write(row["closedDay"],"MATERIAL_GROUP",row["workOrderId"],
                  group_id,g["status"],material=g["materialNetMinor"],
                  source="NET_GROUP_COST",qty=g["netQuantity"],revision=valuation)
            # Source move rows explain quantity and direction ONLY.
            # Cost is deliberately blank, not a guessed per-move valuation.
            for movement in g["movements"]:
                if movement["action"] not in ("ISSUE","RETURN"):
                    raise Conflict()
                write(row["closedDay"],"STOCK",row["workOrderId"],
                      str(UUID(movement["stockEventId"])),g["status"],
                      source=movement["action"],qty=movement["quantity"],
                      revision=group_id)
    body=("\ufeff"+out.getvalue()).encode("utf-8")
    if len(body)>MAX_CSV_BYTES:
        raise Conflict()
    return body


def _run(request,org_id,loc_id,from_day,to_day,*,export=False):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        org,loc=UUID(org_id),UUID(loc_id)
    except (TypeError,ValueError):
        return _response(request,422,"INVALID_INPUT")
    try:
        with psycopg.connect(dsn) as conn:
            data=report(conn,request.cookies.get("rp_session",""),org,loc,
                        from_day,to_day)
        if export:
            return Response(content=_csv(data),
                media_type="text/csv; charset=utf-8",
                headers={"Cache-Control":"no-store",
                         "X-Content-Type-Options":"nosniff",
                         "Content-Disposition":
                           'attachment; filename="auto-closed-contribution.csv"'})
        return JSONResponse(content=data,headers={"Cache-Control":"no-store"})
    except Denied:
        return _response(request,403,"FORBIDDEN")
    except (Conflict,psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
        return _response(request,409,"REPORT_CONFLICT")
    except ValueError:
        return _response(request,422,"INVALID_INPUT")


@router.get(PREFIX)
def get_closed_contribution(org_id:str,loc_id:str,request:Request,
                            fromDay:str,toDay:str):
    return _run(request,org_id,loc_id,fromDay,toDay)

@router.get(PREFIX+"/export")
def export_closed_contribution(org_id:str,loc_id:str,request:Request,
                               fromDay:str,toDay:str):
    return _run(request,org_id,loc_id,fromDay,toDay,export=True)
