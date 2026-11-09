"""Synthetic automotive estimate: immutable priced service snapshot and owner decision.

No real billing, payment, signature or production consent. The WorkOrder lock
serializes re-pricing and final decisions, with current proof checked each time.
"""
import hashlib
import json
from uuid import UUID, uuid4
import psycopg
from .auto_workshop_qc import _actor_and_order, InspectionDenied
from .session_lookup import session_subject
from .auto_org_authority import organization_role_allows
from .auto_work_order_diagnostics import diagnostic_snapshot
from .auto_work_tariffs import quote_tariff_snapshot,TariffConflict


class AutoEstimateDenied(ValueError):
    pass


class AutoEstimateConflict(ValueError):
    pass


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,separators=(",",":"),
                                     ensure_ascii=True).encode()).hexdigest()


def _client_and_scope(conn, token, org, loc, order):
    if (type(token) is not str or not token.strip() or
        any(type(i) is not UUID for i in (org,loc,order))):
        raise AutoEstimateDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise AutoEstimateDenied("FORBIDDEN")
    row=conn.execute("""
      SELECT w.id FROM auto_work_order w
      JOIN auto_appointment a ON a.id=w.appointment_id
      JOIN LATERAL (
        SELECT COALESCE((
          SELECT r.new_booking_id FROM auto_appointment_reschedule r
          WHERE r.appointment_id=a.id
          ORDER BY r.created_at DESC,r.id DESC LIMIT 1
        ),a.booking_id) AS current_id
      ) current_booking ON TRUE
      JOIN auto_bay_booking b ON b.id=current_booking.current_id
      JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
        AND proof.vehicle_id=a.vehicle_id
      JOIN account_customer_link acl ON acl.customer_id=proof.customer_id
        AND acl.user_id=%s AND acl.revoked_at IS NULL
      JOIN user_role role ON role.user_id=%s AND role.role_code='CLIENT'
        AND role.revoked_at IS NULL
        AND (role.expires_at IS NULL OR role.expires_at>now())
      JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
        AND binding.location_id=w.location_id
      JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
      JOIN auto_service_location loc ON loc.id=w.location_id
        AND loc.organization_id=o.id AND loc.disabled_at IS NULL
      WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
        AND a.organization_id=w.organization_id
        AND a.location_id=w.location_id
        AND b.organization_id=w.organization_id
        AND b.location_id=w.location_id AND b.canceled_at IS NULL
        AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
      FOR UPDATE OF w
    """,(actor,actor,order,org,loc)).fetchone()
    if row is None:
        raise AutoEstimateDenied("FORBIDDEN")
    return actor


def _staff_and_scope(conn,token,org,loc,order):
    try:
        return _actor_and_order(conn,token,org,loc,order,("MANAGER",))
    except InspectionDenied:
        raise AutoEstimateDenied("FORBIDDEN") from None


def _complex_material_snapshot(conn,order):
    """Frozen priced PARTs and non-sold CONSUMABLEs from actual reserved stock."""
    rows=conn.execute("""
      SELECT sel.id,sku.id,sku.article,component.item_kind,
             component.quantity,component.unit_price_minor
      FROM auto_work_order_complex_selection sel
      JOIN auto_work_complex_material_reserved receipt
        ON receipt.selection_id=sel.id
      JOIN auto_work_complex_material component
        ON component.id=receipt.component_id
      JOIN auto_stock_sku sku ON sku.id=component.sku_id
      WHERE sel.work_order_id=%s ORDER BY sku.article,sku.id,sel.id LIMIT 101
    """,(order,)).fetchall()
    if len(rows)>100:
        raise AutoEstimateConflict("MATERIAL_LIMIT")
    return [{"selectionId":str(selection),"skuId":str(sku),"article":article,
             "itemKind":kind,"quantity":quantity,"unitPriceMinor":unit,
             "amountMinor":quantity*unit}
            for selection,sku,article,kind,quantity,unit in rows]


def _estimate_state(conn,order):
    revisions=conn.execute("""
       SELECT r.id,r.version,r.total_minor,r.fingerprint,r.service_lines,
              r.created_at,d.decision,d.created_at,
              q.id,q.reason_code,answer.resolution_code,r.diagnostics_snapshot,
              r.tariff_snapshot,r.material_lines
       FROM auto_work_order_estimate_revision r
       LEFT JOIN auto_work_order_estimate_decision d ON d.revision_id=r.id
       LEFT JOIN auto_estimate_clarification_request q ON q.revision_id=r.id
       LEFT JOIN auto_estimate_clarification_reply answer ON answer.request_id=q.id
       WHERE r.work_order_id=%s ORDER BY r.version LIMIT 31
    """,(order,)).fetchall()
    if len(revisions)>30:
        raise AutoEstimateConflict("LIMIT")
    results=[]
    for ident,version,total,digest,lines,created,decision,decided_at,question,reason,resolution,diagnostics,tariffs,materials in revisions:
        results.append({"id":str(ident),"version":version,"totalMinor":total,
                        "currency":"RUB","digest":digest.strip(),
                        "lines":lines,"diagnostics":diagnostics,"tariffs":tariffs,
                        "materialLines":materials,
                        "decision":decision,
                        "createdAt":created.isoformat(),
                        "decidedAt":decided_at.isoformat() if decided_at else None,
                        "clarificationId":str(question) if question else None,
                        "clarificationReason":reason,
                        "clarificationResolution":resolution})
    return {"items":results,"latestVersion":results[-1]["version"] if results else 0,
            "syntheticOnly":True,"releaseAllowed":False}


def read_auto_estimates(conn,token,org,loc,order):
    with conn.transaction():
        if organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True):
            _staff_and_scope(conn,token,org,loc,order)
        else:
            _client_and_scope(conn,token,org,loc,order)
        return _estimate_state(conn,order)


def publish_auto_estimate(conn,token,org,loc,order,*,request_key,expected_version,
                          prices=None,labor_rates=None,work_rates=None):
    """Manual prices or server-calculated norm-hour prices; never both.

    Each rated line contains the exact rate, qualification category and source
    duration that the customer approves. A category is a proposed tariff, NOT
    evidence of a real mechanic's accredited qualification.
    """
    advanced=work_rates is not None
    rated=labor_rates is not None or advanced
    selected=work_rates if advanced else labor_rates if rated else prices
    if (type(request_key) is not UUID or type(expected_version) is not int or
        not 0<=expected_version<30 or
        sum(x is not None for x in (prices,labor_rates,work_rates))!=1 or
        type(selected) is not list or not 1<=len(selected)<=50):
        raise AutoEstimateDenied("INVALID_INPUT")
    supplied={}
    for item in selected:
        if type(item) is not dict or type(item.get("serviceId")) is not UUID:
            raise AutoEstimateDenied("INVALID_INPUT")
        service=item["serviceId"]
        if service in supplied:
            raise AutoEstimateDenied("INVALID_INPUT")
        if rated:
            required={"serviceId","qualificationCode","rateMinorPerHour"}
            if advanced:
                required|={"quantity","coefficientBps","discountBps"}
            if (set(item)!=required or
                item["qualificationCode"] not in ("GENERAL","DIAGNOSTIC","MASTER") or
                type(item["rateMinorPerHour"]) is not int or
                not 0<=item["rateMinorPerHour"]<=10**10):
                raise AutoEstimateDenied("INVALID_INPUT")
            if advanced and (
                type(item["quantity"]) is not int or
                not 1<=item["quantity"]<=20 or
                type(item["coefficientBps"]) is not int or
                not 10000<=item["coefficientBps"]<=30000 or
                type(item["discountBps"]) is not int or
                not 0<=item["discountBps"]<=2500):
                raise AutoEstimateDenied("INVALID_INPUT")
        elif (set(item)!={"serviceId","amountMinor"} or
              type(item["amountMinor"]) is not int or
              not 0<=item["amountMinor"]<=10**12):
            raise AutoEstimateDenied("INVALID_INPUT")
        supplied[service]=item
    with conn.transaction():
        actor=_staff_and_scope(conn,token,org,loc,order)
        services=conn.execute("""
           SELECT s.id,s.code,s.title,s.duration_minutes FROM auto_work_order_service link
           JOIN auto_service_catalog s ON s.id=link.service_id
             AND s.organization_id=link.organization_id
             AND s.location_id=link.location_id
           WHERE link.work_order_id=%s
             AND link.organization_id=%s AND link.location_id=%s
           ORDER BY s.code,s.id LIMIT 51
        """,(order,org,loc)).fetchall()
        if len(services)>50 or len(services)!=len(supplied) or set(v[0] for v in services)!=set(supplied):
            raise AutoEstimateConflict("SERVICE_SET_CHANGED")
        lines=[]
        for i,code,title,duration in services:
            line={"serviceId":str(i),"code":code,"title":title,
                  "durationMinutes":duration}
            if rated:
                rate=supplied[i]["rateMinorPerHour"]
                if advanced:
                    quantity=supplied[i]["quantity"]
                    coeff=supplied[i]["coefficientBps"]
                    discount=supplied[i]["discountBps"]
                    # One rounding, after all modifiers: no float/intermediate
                    # kopeck rounding, and PostgreSQL independently recomputes.
                    numerator=duration*rate*quantity*coeff*(10000-discount)
                    amount=(numerator+3000000000)//6000000000
                    line.update({"pricingMethod":"NORM_HOUR_FACTORED",
                                 "qualificationCode":supplied[i]["qualificationCode"],
                                 "rateMinorPerHour":rate,"quantity":quantity,
                                 "coefficientBps":coeff,"discountBps":discount,
                                 "amountMinor":amount})
                else:
                    # Original norm-hour contract is unchanged for old clients.
                    line.update({"pricingMethod":"NORM_HOUR",
                                 "qualificationCode":supplied[i]["qualificationCode"],
                                 "rateMinorPerHour":rate,
                                 "amountMinor":(duration*rate+30)//60})
            else:
                line["amountMinor"]=supplied[i]["amountMinor"]
            lines.append(line)
        materials=_complex_material_snapshot(conn,order)
        total=sum(item["amountMinor"] for item in lines)+sum(item["amountMinor"] for item in materials)
        if total>10**12:
            raise AutoEstimateDenied("INVALID_INPUT")
        try:
            tariffs=quote_tariff_snapshot(conn,org,loc,lines)
        except TariffConflict:
            raise AutoEstimateConflict("TARIFF_MISMATCH") from None
        diagnostics=diagnostic_snapshot(conn,order)
        digest=_fingerprint({"order":str(order),"lines":lines,
                             "diagnostics":diagnostics,"tariffs":tariffs,
                             "materials":materials,"totalMinor":total,
                             "currency":"RUB"})
        existing=conn.execute("""
          SELECT fingerprint FROM auto_work_order_estimate_revision
          WHERE work_order_id=%s AND request_key=%s
        """,(order,request_key)).fetchone()
        if existing is not None:
            if existing[0].strip()!=digest:
                raise AutoEstimateConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**_estimate_state(conn,order)}
        state=_estimate_state(conn,order)
        if state["items"] and state["items"][-1]["clarificationId"] and (
            state["items"][-1]["clarificationResolution"] is None):
            raise AutoEstimateConflict("CLARIFICATION_PENDING")
        if (state["latestVersion"]!=expected_version or
            any(item["decision"] is not None for item in state["items"])):
            raise AutoEstimateConflict("VERSION_CONFLICT")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_estimate_revision
                  (id,work_order_id,organization_id,location_id,version,actor_id,
                   request_key,fingerprint,total_minor,service_lines,
                   diagnostics_snapshot,tariff_snapshot,material_lines)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb)
                """,(uuid4(),order,org,loc,expected_version+1,actor,
                      request_key,digest,total,json.dumps(lines),
                      json.dumps(diagnostics),json.dumps(tariffs),json.dumps(materials)))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoEstimateConflict("VERSION_CONFLICT") from None
        return {"ok":True,"replayed":False,**_estimate_state(conn,order)}


def decide_auto_estimate(conn,token,org,loc,order,*,revision_id,request_key,decision):
    if (type(revision_id) is not UUID or type(request_key) is not UUID
        or decision not in ("APPROVED","DECLINED")):
        raise AutoEstimateDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_client_and_scope(conn,token,org,loc,order)
        state=_estimate_state(conn,order)
        current=state["items"][-1] if state["items"] else None
        if current is None or current["id"]!=str(revision_id):
            raise AutoEstimateConflict("STALE_REVISION")
        if (current["clarificationId"] and
            current["clarificationResolution"]!="EXPLAINED"):
            raise AutoEstimateConflict(
                "CLARIFICATION_PENDING" if current["clarificationResolution"] is None
                else "CLARIFICATION_NEW_VERSION_REQUIRED")
        digest=_fingerprint({"revision":str(revision_id),"actor":str(actor),
                             "decision":decision,"estimateDigest":current["digest"]})
        previous=conn.execute("""
           SELECT actor_id,request_key,decision,fingerprint
           FROM auto_work_order_estimate_decision WHERE revision_id=%s
        """,(revision_id,)).fetchone()
        if previous:
            if previous!=(actor,request_key,decision,digest):
                raise AutoEstimateConflict("ALREADY_DECIDED")
            return {"ok":True,"replayed":True,**_estimate_state(conn,order)}
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_estimate_decision
                  (id,revision_id,work_order_id,actor_id,request_key,decision,fingerprint)
                  VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),revision_id,order,actor,request_key,decision,digest))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoEstimateConflict("ALREADY_DECIDED") from None
        return {"ok":True,"replayed":False,**_estimate_state(conn,order)}


def request_estimate_clarification(conn,token,org,loc,order,*,
                                   revision_id,request_key,reason_code):
    """One structured immutable client question per current undecided version."""
    if (type(revision_id) is not UUID or type(request_key) is not UUID or
        reason_code not in ("PRICE","WORK_SCOPE","TIMING","OTHER")):
        raise AutoEstimateDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_client_and_scope(conn,token,org,loc,order)
        state=_estimate_state(conn,order)
        current=state["items"][-1] if state["items"] else None
        if current is None or current["id"]!=str(revision_id) or current["decision"] is not None:
            raise AutoEstimateConflict("STALE_REVISION")
        previous=conn.execute("""
          SELECT revision_id,actor_id,request_key,reason_code
          FROM auto_estimate_clarification_request WHERE work_order_id=%s AND revision_id=%s
        """,(order,revision_id)).fetchone()
        if previous:
            if previous!=(revision_id,actor,request_key,reason_code):
                raise AutoEstimateConflict("CLARIFICATION_ALREADY_REQUESTED")
            return {"ok":True,"replayed":True,**_estimate_state(conn,order)}
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_estimate_clarification_request
                  (id,revision_id,work_order_id,actor_id,request_key,reason_code)
                  VALUES(%s,%s,%s,%s,%s,%s)
                """,(uuid4(),revision_id,order,actor,request_key,reason_code))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoEstimateConflict("CLARIFICATION_ALREADY_REQUESTED") from None
        return {"ok":True,"replayed":False,**_estimate_state(conn,order)}


def resolve_estimate_clarification(conn,token,org,loc,order,*,
                                   revision_id,request_key,resolution_code):
    """Manager resolves current client question, without silently editing estimate."""
    if (type(revision_id) is not UUID or type(request_key) is not UUID or
        resolution_code not in ("EXPLAINED","NEW_VERSION_REQUIRED")):
        raise AutoEstimateDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_staff_and_scope(conn,token,org,loc,order)
        state=_estimate_state(conn,order)
        current=state["items"][-1] if state["items"] else None
        if current is None or current["id"]!=str(revision_id) or current["decision"] is not None:
            raise AutoEstimateConflict("STALE_REVISION")
        if not current["clarificationId"]:
            raise AutoEstimateConflict("CLARIFICATION_REQUIRED")
        prior=conn.execute("""
          SELECT answer.actor_id,answer.request_key,answer.resolution_code
          FROM auto_estimate_clarification_reply answer
          JOIN auto_estimate_clarification_request q ON q.id=answer.request_id
          WHERE q.revision_id=%s
        """,(revision_id,)).fetchone()
        if prior:
            if prior!=(actor,request_key,resolution_code):
                raise AutoEstimateConflict("CLARIFICATION_ALREADY_RESOLVED")
            return {"ok":True,"replayed":True,**_estimate_state(conn,order)}
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_estimate_clarification_reply
                  (id,request_id,actor_id,request_key,resolution_code)
                  VALUES(%s,%s,%s,%s,%s)
                """,(uuid4(),UUID(current["clarificationId"]),actor,
                      request_key,resolution_code))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoEstimateConflict("CLARIFICATION_ALREADY_RESOLVED") from None
        return {"ok":True,"replayed":False,**_estimate_state(conn,order)}
