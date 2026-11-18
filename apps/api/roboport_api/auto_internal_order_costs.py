"""Внутренняя расчётная ставка и сверка затрат закрытого заказ-наряда (#448).

Только synthetic dev. Не начисление зарплаты, реальная выручка или COGS.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from io import StringIO
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth_http import _mutation_ok
from .auto_closed_order_report import report as closed_report, Denied, Conflict, MAX_SAFE
from .auto_closed_order_materials import _material_report
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_org_authority import organization_role_allows
from .auto_vehicle_claim_http import _request_dsn, _response
from .session_lookup import session_subject

router = APIRouter(include_in_schema=False)
ROOT = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
POLICY = ROOT + "/internal-labor-cost-policies"
REPORT = ROOT + "/reports/internal-order-costs"
MAX_LABOR = 500
BASIS = "CLOSED_TECHNICAL_TRACED_MATERIAL_AND_APPROVED_INTERNAL_LABOR_COST"


def _authority(conn, token, org, loc, write=False):
    # Редактирование занимает mutex филиала до SHARE-проверок.
    if write:
        if conn.execute("""SELECT id FROM auto_organization
            WHERE id=%s AND disabled_at IS NULL FOR SHARE""", (org,)).fetchone() is None:
            raise Denied()
        if conn.execute("""SELECT id FROM auto_service_location
            WHERE id=%s AND organization_id=%s AND disabled_at IS NULL FOR UPDATE""",
            (loc, org)).fetchone() is None:
            raise Denied()
    if not organization_role_allows(conn, token, org, "MANAGER", loc, for_mutation=True):
        raise Denied()
    try:
        for cap in ("READ", "PRICE_PURCHASE", *(("EDIT",) if write else ())):
            if require_document_access(conn, token, org, loc, ("MANAGER", "CASHIER"),
                                       "FINANCE", cap) is not None:
                raise Denied()
    except DocumentPolicyDenied:
        raise Denied() from None
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise Denied()
    return actor


def _state(conn, org, loc):
    rows = conn.execute("""
      SELECT p.id,p.version,p.rate_minor_per_hour,p.actor_id,p.created_at,
             a.id,a.approved_by,a.created_at
      FROM auto_internal_labor_cost_revision p
      LEFT JOIN auto_internal_labor_cost_approval a ON a.revision_id=p.id
      WHERE p.organization_id=%s AND p.location_id=%s
      ORDER BY p.version LIMIT 101
    """, (org, loc)).fetchall()
    if len(rows)>100:
        raise Conflict()
    items = [{"id":str(r[0]),"version":r[1],"rateMinorPerHour":r[2],
              "proposedBy":str(r[3]),"createdAt":r[4].isoformat(),
              "approvalId":str(r[5]) if r[5] else None,
              "approvedBy":str(r[6]) if r[6] else None,
              "approvedAt":r[7].isoformat() if r[7] else None} for r in rows]
    active = next((p for p in reversed(items) if p["approvalId"]), None)
    return {"syntheticOnly":True,"payrollAvailable":False,"currency":"RUB",
            "items":items,"active":active}


def read_policies(conn, token, org, loc):
    with conn.transaction():
        _authority(conn, token, org, loc)
        return _state(conn, org, loc)


def propose(conn, token, org, loc, key, expected_version, rate):
    if (any(type(x) is not UUID for x in (org,loc,key)) or
        type(expected_version) is not int or not 0<=expected_version<100 or
        type(rate) is not int or not 0<=rate<=10**10):
        raise ValueError("INVALID_INPUT")
    with conn.transaction():
        actor = _authority(conn, token, org, loc, write=True)
        prior = conn.execute("""
          SELECT organization_id,location_id,actor_id,version,rate_minor_per_hour
          FROM auto_internal_labor_cost_revision WHERE request_key=%s
        """, (key,)).fetchone()
        if prior:
            if prior != (org,loc,actor,expected_version+1,rate):
                raise Conflict()
            return {"replayed":True,**_state(conn,org,loc)}
        state = _state(conn,org,loc)
        if len(state["items"])!=expected_version or (
            state["items"] and not state["items"][-1]["approvalId"]
        ):
            raise Conflict()
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_internal_labor_cost_revision
                    (id,organization_id,location_id,version,rate_minor_per_hour,actor_id,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """, (uuid4(),org,loc,expected_version+1,rate,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise Conflict() from None
        return {"replayed":False,**_state(conn,org,loc)}


def approve(conn, token, org, loc, ident, key):
    if any(type(x) is not UUID for x in (org,loc,ident,key)):
        raise ValueError("INVALID_INPUT")
    with conn.transaction():
        actor = _authority(conn, token, org, loc, write=True)
        row = conn.execute("""
          SELECT p.actor_id,a.approved_by,a.request_key
          FROM auto_internal_labor_cost_revision p
          LEFT JOIN auto_internal_labor_cost_approval a ON a.revision_id=p.id
          WHERE p.id=%s AND p.organization_id=%s AND p.location_id=%s
        """, (ident,org,loc)).fetchone()
        if row is None or actor==row[0]:
            raise Denied()
        if row[1] is not None:
            if (row[1],row[2])!=(actor,key):
                raise Conflict()
            return {"replayed":True,**_state(conn,org,loc)}
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_internal_labor_cost_approval
                    (id,revision_id,approved_by,request_key) VALUES(%s,%s,%s,%s)
                """, (uuid4(),ident,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            raise Conflict() from None
        return {"replayed":False,**_state(conn,org,loc)}


def _bounded(n):
    if type(n) is not int or abs(n)>MAX_SAFE:
        raise Conflict()
    return n


def _load_labor(conn, ids, org, loc):
    return conn.execute("""
      SELECT e.work_order_id,l.id,l.service_id,l.actual_minutes,
             l.internal_cost_approval_id,r.id,r.version,r.rate_minor_per_hour,
             (r.organization_id=e.organization_id AND r.location_id=e.location_id
              AND a.created_at<=l.internal_cost_registered_at) AS valid_rate,
             EXISTS(SELECT 1 FROM app_user u
              JOIN auto_organization_role_grant g ON g.user_id=u.id
              WHERE u.id=l.actor_id AND u.status='ACTIVE'
                AND g.organization_id=e.organization_id
                AND g.role_code='MECHANIC' AND g.revoked_at IS NULL
                AND (g.location_id=e.location_id OR g.location_id IS NULL)
                FOR SHARE OF u,g) AS live_mechanic
      FROM auto_work_order_execution e
      JOIN auto_work_order_labor_entry l ON l.execution_id=e.id
      JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
      LEFT JOIN auto_internal_labor_cost_approval a ON a.id=l.internal_cost_approval_id
      LEFT JOIN auto_internal_labor_cost_revision r ON r.id=a.revision_id
      WHERE e.work_order_id=ANY(%s::uuid[])
        AND e.organization_id=%s AND e.location_id=%s
      ORDER BY e.work_order_id,l.id LIMIT %s
    """, (ids,org,loc,MAX_LABOR+1)).fetchall()


def cost_report(conn, token, org, loc, from_day, to_day):
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        _authority(conn,token,org,loc)
        base=closed_report(conn,token,org,loc,from_day,to_day)
        materials=_material_report(conn,token,org,loc,from_day,to_day,base)
        ids=[UUID(x["workOrderId"]) for x in base["items"]]
        rows=_load_labor(conn,ids,org,loc)
        if len(rows)>MAX_LABOR:
            raise Conflict()
        labor=defaultdict(list)
        for order,entry,service,minutes,approval,revision,version,rate,valid,live in rows:
            if type(minutes) is not int or not 1<=minutes<=1440:
                raise Conflict()
            if approval is not None and (
                 valid is not True or type(rate) is not int or not 0<=rate<=10**10
            ):
                raise Conflict()
            status="INACTIVE_MECHANIC" if not live else (
                "TRACED" if approval is not None else "NO_APPROVED_RATE")
            # Округление half-up отдельно для каждой immutable записи.
            amount=_bounded((minutes*rate+30)//60) if status=="TRACED" else None
            labor[str(order)].append({
                "laborEntryId":str(entry),"serviceId":str(service),"actualMinutes":minutes,
                "status":status,"costMinor":amount,
                "source":{"approvalId":str(approval),"revisionId":str(revision),
                          "version":version,"rateMinorPerHour":rate}
                  if status=="TRACED" else None,
            })
        by_order={x["workOrderId"]:x for x in materials["items"]}
        items=[]
        for x in base["items"]:
            order=x["workOrderId"]
            mat=by_order[order]
            entries=labor[order]
            # Отсутствие труда в закрытом наряде не равно нулевой стоимости.
            labor_cost=(_bounded(sum(e["costMinor"] for e in entries))
                if entries and all(e["status"]=="TRACED" for e in entries) else None)
            material_cost=mat["materialNetMinor"]
            total=(_bounded(labor_cost+material_cost)
                   if labor_cost is not None and material_cost is not None else None)
            delta=_bounded(x["approvedTotalMinor"]-total) if total is not None else None
            items.append({
                "workOrderId":order,"closedDay":x["closedDay"],
                "closedEventId":x["closedEventId"],"previewId":x["previewId"],
                "previewDigest":x["previewDigest"],"approvedTotalMinor":x["approvedTotalMinor"],
                "materialNetMinor":material_cost,"laborCostMinor":labor_cost,
                "directCostMinor":total,"syntheticDifferenceMinor":delta,
                "status":"COMPLETE" if total is not None else "INCOMPLETE",
                "laborEntries":entries,"materialGroups":mat["groups"],
            })
        def sum_complete(key):
            values=[row[key] for row in items]
            return _bounded(sum(values)) if all(v is not None for v in values) else None
        return {
            "syntheticOnly":True,"realMoney":False,"releaseAllowed":False,
            "marginAvailable":False,"payrollAvailable":False,"currency":"RUB",
            "basis":BASIS,"timeZone":base["timeZone"],"fromDay":from_day,"toDay":to_day,
            "summary":{"orderCount":len(items),
                       "completeOrderCount":sum(x["status"]=="COMPLETE" for x in items),
                       "laborEntryCount":len(rows),
                       **{k:sum_complete(k) for k in (
                           "approvedTotalMinor","materialNetMinor","laborCostMinor",
                           "directCostMinor","syntheticDifferenceMinor")}},
            "items":items,
        }


def _csv(data):
    out=StringIO(newline="")
    w=csv.writer(out,delimiter=";",lineterminator="\r\n")
    def emit(fields):
        if len(fields)>30:
            raise Conflict()
        w.writerow((*fields, *[""]*(30-len(fields))))
    emit(("Заказ UUID","Закрытие UUID","Уровень","Статус","Утверждено, коп.",
                "Материалы, коп.","Труд, коп.","Прямые затраты, коп.","Условная разница, коп.",
                "Запись труда UUID","Услуга UUID","Минуты","Ставка UUID","Утверждение ставки UUID",
                "Версия","Ставка, коп./час","Партия UUID","SKU UUID","Приход UUID",
                "Приёмка UUID","Оценка UUID","Цена единицы, коп.","Складское событие UUID",
                "Движение партии UUID","Действие","Количество","Исходная выдача UUID",
                "Серийная деталь UUID","Подтверждение происхождения UUID", "Филиал-источник UUID"))
    for x in data["items"]:
        scope=(x["workOrderId"],x["closedEventId"])
        emit((*scope,"ORDER",x["status"],x["approvedTotalMinor"],x["materialNetMinor"],
                    x["laborCostMinor"],x["directCostMinor"],x["syntheticDifferenceMinor"],
                    *[""]*20))
        for l in x["laborEntries"]:
            s=l["source"] or {}
            emit((*scope,"LABOR",l["status"],"","",l["costMinor"],"","",
                        l["laborEntryId"],l["serviceId"],l["actualMinutes"],s.get("revisionId"),
                        s.get("approvalId"),s.get("version"),s.get("rateMinorPerHour"),*[""]*13))
        for g in x["materialGroups"]:
            s=g["source"] or {}
            emit((*scope,"LOT",g["status"],"",g["materialNetMinor"],"","","",
                        *[""]*7,g["lotId"],g["skuId"],s.get("receiptId"),s.get("acceptanceId"),
                        s.get("valuationId"),s.get("unitNetMinor"),"","","","","","",""))
            for m in g["movements"]:
                emit((*scope,"MOVEMENT","","","","","","",*[""]*7,g["lotId"],g["skuId"],
                            "","","","",m["stockEventId"],m["lotMovementId"],m["action"],m["quantity"],"","",""))
            for s in g["fifoSlices"]:
                emit((*scope,"FIFO_SLICE","","",s["quantity"]*s["unitNetMinor"]*(1 if s["action"]=="ISSUE" else -1),
                            "","","",*[""]*7,g["lotId"],g["skuId"],s["receiptId"],s["acceptanceId"],
                            s["valuationId"],s["unitNetMinor"],s["stockEventId"],s["lotMovementId"],
                            s["action"],s["quantity"],s["originalIssueId"],
                            s.get("serialId",""),s.get("serialApprovalId",""),s.get("sourceLocationId","")))
    return "\ufeff"+out.getvalue()


class Proposal(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID
    expectedVersion:int=Field(ge=0,le=99,strict=True)
    rateMinorPerHour:int=Field(ge=0,le=10**10,strict=True)


class Approval(BaseModel):
    model_config=ConfigDict(extra="forbid")
    requestKey:UUID


def _scope(request,org_id,loc_id):
    dsn=_request_dsn(request)
    if isinstance(dsn,JSONResponse):
        return dsn
    try:
        return dsn,UUID(org_id),UUID(loc_id)
    except ValueError:
        return _response(request,422,"INVALID_INPUT")


def _result(request,scope,operation,*,write=False,export=False):
    if isinstance(scope,JSONResponse):
        return scope
    dsn,org,loc=scope
    try:
        with psycopg.connect(dsn) as db:
            if write and not _mutation_ok(request,db):
                return _response(request,403,"FORBIDDEN")
            data=operation(db,request.cookies.get("rp_session",""),org,loc)
    except Denied:
        return _response(request,403,"FORBIDDEN")
    except (Conflict,psycopg.errors.SerializationFailure,psycopg.errors.DeadlockDetected):
        return _response(request,409,"REPORT_CONFLICT")
    except ValueError:
        return _response(request,422,"INVALID_INPUT")
    if export:
        return Response(content=_csv(data),media_type="text/csv; charset=utf-8",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                                 "Content-Disposition":'attachment; filename="auto-internal-order-costs.csv"'})
    return JSONResponse(content=data,status_code=201 if write and not data["replayed"] else 200,
                        headers={"Cache-Control":"no-store"})


@router.get(POLICY)
def policies(org_id:str,loc_id:str,request:Request):
    return _result(request,_scope(request,org_id,loc_id),read_policies)


@router.post(POLICY)
async def propose_policy(org_id:str,loc_id:str,request:Request):
    scope=_scope(request,org_id,loc_id)
    if isinstance(scope,JSONResponse):
        return scope
    try:
        p=Proposal.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _result(request,scope,lambda db,t,o,l:propose(
        db,t,o,l,p.requestKey,p.expectedVersion,p.rateMinorPerHour),write=True)


@router.post(POLICY+"/{policy_id}/approve")
async def approve_policy(org_id:str,loc_id:str,policy_id:str,request:Request):
    scope=_scope(request,org_id,loc_id)
    if isinstance(scope,JSONResponse):
        return scope
    try:
        ident=UUID(policy_id)
        p=Approval.model_validate(await request.json())
    except (ValueError,TypeError,ValidationError,UnicodeDecodeError):
        return _response(request,422,"INVALID_INPUT")
    return _result(request,scope,lambda db,t,o,l:approve(
        db,t,o,l,ident,p.requestKey),write=True)


@router.get(REPORT)
def report(org_id:str,loc_id:str,request:Request,fromDay:str,toDay:str):
    return _result(request,_scope(request,org_id,loc_id),
                   lambda db,t,o,l:cost_report(db,t,o,l,fromDay,toDay))


@router.get(REPORT+"/export")
def export_report(org_id:str,loc_id:str,request:Request,fromDay:str,toDay:str):
    return _result(request,_scope(request,org_id,loc_id),
                   lambda db,t,o,l:cost_report(db,t,o,l,fromDay,toDay),export=True)
