"""Separately approved extra labor scope for a synthetic work order.

The original estimate and service selections never change. Extra versions are
immutable snapshots, and only an independent CLIENT decision can enable labor.
"""
import json
from uuid import UUID,uuid4

import psycopg

from .auto_work_order_estimate import (
    _staff_and_scope,_client_and_scope,_estimate_state,_fingerprint,
    AutoEstimateDenied,
)


class ExtraDenied(ValueError):
    pass


class ExtraConflict(ValueError):
    pass


def _state(conn,order):
    rows=conn.execute("""
      SELECT r.id,r.version,r.base_revision_id,r.total_minor,r.fingerprint,
             r.service_lines,r.created_at,d.decision,d.created_at
      FROM auto_work_order_extra_revision r
      LEFT JOIN auto_work_order_extra_decision d ON d.revision_id=r.id
      WHERE r.work_order_id=%s ORDER BY r.version LIMIT 11
    """,(order,)).fetchall()
    if len(rows)>10:
        raise ExtraConflict("LIMIT")
    items=[{
        "id":str(r[0]),"version":r[1],"baseRevisionId":str(r[2]),
        "totalMinor":r[3],"currency":"RUB","digest":r[4].strip(),
        "lines":r[5],"createdAt":r[6].isoformat(),
        "decision":r[7],"decidedAt":r[8].isoformat() if r[8] else None,
    } for r in rows]
    return {"items":items,"latestVersion":items[-1]["version"] if items else 0,
            "syntheticOnly":True,"releaseAllowed":False}


def _authorize(conn,token,org,loc,order,manager):
    try:
        return (_staff_and_scope(conn,token,org,loc,order) if manager
                else _client_and_scope(conn,token,org,loc,order))
    except AutoEstimateDenied:
        raise ExtraDenied("FORBIDDEN") from None


def read_extra(conn,token,org,loc,order,manager):
    with conn.transaction():
        _authorize(conn,token,org,loc,order,manager)
        return _state(conn,order)


def publish_extra(conn,token,org,loc,order,*,base_revision_id,
                  request_key,expected_version,work_rates):
    if (any(type(x) is not UUID for x in (base_revision_id,request_key))
        or type(expected_version) is not int or not 0<=expected_version<10
        or type(work_rates) is not list or not 1<=len(work_rates)<=20):
        raise ExtraDenied("INVALID_INPUT")
    supplied={}
    for item in work_rates:
        if (type(item) is not dict
            or set(item)!={"serviceId","qualificationCode","rateMinorPerHour",
                           "quantity","coefficientBps","discountBps"}
            or type(item.get("serviceId")) is not UUID
            or item["qualificationCode"] not in ("GENERAL","DIAGNOSTIC","MASTER")
            or type(item["rateMinorPerHour"]) is not int
            or not 0<=item["rateMinorPerHour"]<=10**10
            or type(item["quantity"]) is not int
            or not 1<=item["quantity"]<=20
            or type(item["coefficientBps"]) is not int
            or not 10000<=item["coefficientBps"]<=30000
            or type(item["discountBps"]) is not int
            or not 0<=item["discountBps"]<=2500
            or item["serviceId"] in supplied):
            raise ExtraDenied("INVALID_INPUT")
        supplied[item["serviceId"]]=item
    with conn.transaction():
        actor=_authorize(conn,token,org,loc,order,True)
        base=_estimate_state(conn,order)["items"]
        if not base or base[-1]["id"]!=str(base_revision_id) or base[-1]["decision"]!="APPROVED":
            raise ExtraConflict("BASE_APPROVAL_REQUIRED")
        if conn.execute("""
          SELECT 1 FROM auto_work_order_execution_finish f
          JOIN auto_work_order_execution e ON e.id=f.execution_id
          WHERE e.work_order_id=%s
        """,(order,)).fetchone():
            raise ExtraConflict("EXECUTION_FINISHED")
        existing_services={UUID(line["serviceId"]) for line in base[-1]["lines"]}
        if existing_services.intersection(supplied):
            raise ExtraConflict("SERVICE_ALREADY_APPROVED")
        rows=conn.execute("""
          SELECT id,code,title,duration_minutes
          FROM auto_service_catalog
          WHERE organization_id=%s AND location_id=%s AND id=ANY(%s)
          ORDER BY code,id FOR SHARE
        """,(org,loc,list(supplied))).fetchall()
        if len(rows)!=len(supplied):
            raise ExtraConflict("SERVICE_SCOPE_CHANGED")
        lines=[]
        for ident,code,title,duration in rows:
            item=supplied[ident]
            numerator=(duration*item["rateMinorPerHour"]*item["quantity"]*
                       item["coefficientBps"]*(10000-item["discountBps"]))
            amount=(numerator+3000000000)//6000000000
            lines.append({"serviceId":str(ident),"code":code,"title":title,
                "durationMinutes":duration,"pricingMethod":"NORM_HOUR_FACTORED",
                "qualificationCode":item["qualificationCode"],
                "rateMinorPerHour":item["rateMinorPerHour"],
                "quantity":item["quantity"],"coefficientBps":item["coefficientBps"],
                "discountBps":item["discountBps"],"amountMinor":amount})
        total=sum(line["amountMinor"] for line in lines)
        if total>10**12:
            raise ExtraDenied("INVALID_INPUT")
        digest=_fingerprint({"order":str(order),"baseRevisionId":str(base_revision_id),
                             "lines":lines,"totalMinor":total,"currency":"RUB"})
        prior=conn.execute("""
          SELECT fingerprint FROM auto_work_order_extra_revision
          WHERE work_order_id=%s AND request_key=%s
        """,(order,request_key)).fetchone()
        if prior:
            if prior[0].strip()!=digest:
                raise ExtraConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_state(conn,order)}
        state=_state(conn,order)
        if state["latestVersion"]!=expected_version or any(
            row["decision"]=="APPROVED" for row in state["items"]):
            raise ExtraConflict("VERSION_CONFLICT")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_extra_revision
                  (id,work_order_id,organization_id,location_id,base_revision_id,
                   version,actor_id,request_key,fingerprint,total_minor,service_lines)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,(uuid4(),order,org,loc,base_revision_id,
                    expected_version+1,actor,request_key,digest,total,json.dumps(lines)))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise ExtraConflict("VERSION_CONFLICT") from None
        return {"ok":True,"replayed":False,**_state(conn,order)}


def decide_extra(conn,token,org,loc,order,*,revision_id,request_key,decision):
    if (type(revision_id) is not UUID or type(request_key) is not UUID
        or decision not in ("APPROVED","DECLINED")):
        raise ExtraDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_authorize(conn,token,org,loc,order,False)
        state=_state(conn,order)
        current=state["items"][-1] if state["items"] else None
        if current is None or current["id"]!=str(revision_id):
            raise ExtraConflict("STALE_REVISION")
        digest=_fingerprint({"revision":str(revision_id),"actor":str(actor),
                             "decision":decision,"extraDigest":current["digest"]})
        prior=conn.execute("""
          SELECT actor_id,request_key,decision,fingerprint
          FROM auto_work_order_extra_decision WHERE revision_id=%s
        """,(revision_id,)).fetchone()
        if prior:
            if prior!=(actor,request_key,decision,digest):
                raise ExtraConflict("ALREADY_DECIDED")
            return {"ok":True,"replayed":True,**_state(conn,order)}
        if conn.execute("""
          SELECT 1 FROM auto_work_order_execution_finish f
          JOIN auto_work_order_execution e ON e.id=f.execution_id
          WHERE e.work_order_id=%s
        """,(order,)).fetchone():
            raise ExtraConflict("EXECUTION_FINISHED")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_extra_decision
                    (id,revision_id,work_order_id,actor_id,request_key,decision,fingerprint)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),revision_id,order,actor,request_key,decision,digest))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise ExtraConflict("ALREADY_DECIDED") from None
        return {"ok":True,"replayed":False,**_state(conn,order)}
