"""No-money automotive demo reconciliation and sealed pre-handover checklist.

This is not a financial posting, payment, legal delivery or fiscal document.
Every operation is bound to live WorkOrder, role, vehicle proof and session.
"""
import json
from uuid import UUID,uuid4
import psycopg
from .auto_work_order_execution import _scope, _state, ExecutionDenied
from .auto_work_order_estimate import _client_and_scope, _estimate_state, AutoEstimateDenied
from .auto_work_order_additional import _state as _extra_state
from .auto_org_authority import organization_role_allows
from .auto_work_order_stock import mutate_work_order_stock
from .auto_stock_service import AutoStockDenied, AutoStockConflict
from .auto_document_access import require_document_access,DocumentPolicyDenied


class DemoCloseDenied(ValueError):
    pass


class DemoCloseConflict(ValueError):
    pass


def _authority(conn, token, org, loc, order):
    if type(token) is not str or not token.strip() or any(
            type(x) is not UUID for x in (org,loc,order)):
        raise DemoCloseDenied("FORBIDDEN")
    if (organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)
            or organization_role_allows(conn,token,org,"WAREHOUSE",loc,for_mutation=True)):
        try:
            _scope(conn,token,org,loc,order,("MANAGER","WAREHOUSE"))
            require_document_access(conn,token,org,loc,
                                    ("MANAGER","WAREHOUSE"),"HANDOVER","READ",order=order)
            require_document_access(conn,token,org,loc,
                                    ("MANAGER","WAREHOUSE"),"HANDOVER","PRICE_SALES",order=order)
        except (ExecutionDenied,DocumentPolicyDenied):
            raise DemoCloseDenied("FORBIDDEN") from None
        return
    try:
        _client_and_scope(conn,token,org,loc,order)
    except AutoEstimateDenied:
        raise DemoCloseDenied("FORBIDDEN") from None


def _manager(conn,token,org,loc,order,*,cap="CREATE"):
    try:
        actor=_scope(conn,token,org,loc,order,("MANAGER",))
        require_document_access(conn,token,org,loc,
                                ("MANAGER",),"HANDOVER",cap,order=order)
        require_document_access(conn,token,org,loc,
                                ("MANAGER",),"HANDOVER","PRICE_SALES",order=order)
        return actor
    except (ExecutionDenied,DocumentPolicyDenied):
        raise DemoCloseDenied("FORBIDDEN") from None


def _warehouse(conn,token,org,loc,order):
    try:
        actor=_scope(conn,token,org,loc,order,("WAREHOUSE",))
        require_document_access(conn,token,org,loc,
                                ("WAREHOUSE",),"HANDOVER","EDIT",order=order)
        return actor
    except (ExecutionDenied,DocumentPolicyDenied):
        raise DemoCloseDenied("FORBIDDEN") from None


def _state_snapshot(conn,order):
    execution=_state(conn,order)
    estimates=_estimate_state(conn,order)["items"]
    latest=estimates[-1] if estimates else None
    approved=latest if latest and latest["decision"]=="APPROVED" else None
    # Допработы попадают в итог только после отдельного решения клиента
    # по последней версии, привязанной к принятой базовой смете.
    extras=_extra_state(conn,order)["items"]
    newest_extra=extras[-1] if extras else None
    accepted_extra=(newest_extra if newest_extra and approved and
                    newest_extra["decision"]=="APPROVED" and
                    newest_extra["baseRevisionId"]==approved["id"] else None)
    base_total=approved["totalMinor"] if approved else None
    extra_total=accepted_extra["totalMinor"] if accepted_extra else 0
    rows=conn.execute("""
       SELECT id,kind,amount_minor,created_at
       FROM auto_order_demo_cash_event WHERE work_order_id=%s
       ORDER BY created_at,id LIMIT 101
    """,(order,)).fetchall()
    if len(rows)>100:
        raise DemoCloseConflict("EVENT_LIMIT")
    events=[{"id":str(i),"kind":k,"amountMinor":a,"createdAt":t.isoformat()}
            for i,k,a,t in rows]
    simulated_paid=sum(x["amountMinor"] for x in events if x["kind"]=="SIM_PAYMENT")
    simulated_refunded=sum(x["amountMinor"] for x in events if x["kind"]=="SIM_REFUND")
    total=base_total+extra_total if base_total is not None else None
    due=total-simulated_paid+simulated_refunded if total is not None else None
    balances=conn.execute("""
      SELECT sku_id,
        SUM(CASE WHEN action='RESERVE' THEN quantity
                 WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                 ELSE 0 END)::bigint AS reserved
      FROM auto_work_order_stock_event
      WHERE work_order_id=%s GROUP BY sku_id
      ORDER BY sku_id LIMIT 101
    """,(order,)).fetchall()
    if len(balances)>100:
        raise DemoCloseConflict("STOCK_LIMIT")
    reserved=[{"skuId":str(i),"quantity":int(q)} for i,q in balances if q]
    unreceived=sum(1 for x in execution["materials"] if not x["received"])
    blockers=[]
    if not approved:
        blockers.append("LATEST_ESTIMATE_NOT_APPROVED")
    if newest_extra and newest_extra["decision"] is None:
        blockers.append("ADDITIONAL_CONSENT_PENDING")
    if newest_extra and newest_extra["decision"]=="APPROVED" and not accepted_extra:
        blockers.append("EXTRA_BASE_REVISION_MISMATCH")
    if execution["status"] not in ("QC_ACCEPTED",):
        blockers.append("EXECUTION_OR_INDEPENDENT_QC_INCOMPLETE")
    if unreceived:
        blockers.append("MATERIAL_HANDOFF_OPEN")
    if reserved:
        blockers.append("STOCK_RESERVATION_OPEN")
    if due is None or due!=0:
        blockers.append("SIMULATED_BALANCE_OPEN")
    document=conn.execute("""
      SELECT id,revision_id,fingerprint,snapshot,created_at
      FROM auto_order_demo_handover_preview WHERE work_order_id=%s
    """,(order,)).fetchone()
    doc=None if document is None else {
      "id":str(document[0]),"revisionId":str(document[1]),
      "digest":document[2].strip(),"snapshot":document[3],
      "createdAt":document[4].isoformat(),
    }
    if doc:
        blockers=[]  # The sealed snapshot is immutable; its historical checks passed.
    from .auto_document_lifecycle import _state as document_state
    lifecycle=document_state(conn,order)
    return {"documentWorkflow":lifecycle,
            "syntheticOnly":True,"releaseAllowed":False,"legalGatePassed":False,
            "currency":"RUB","approvedRevisionId":approved["id"] if approved else None,
            "approvedVersion":approved["version"] if approved else None,
            "approvedBaseMinor":base_total,"approvedExtraMinor":extra_total,
            "approvedExtraRevisionId":accepted_extra["id"] if accepted_extra else None,
            "approvedExtraVersion":accepted_extra["version"] if accepted_extra else None,
            "approvedExtraDigest":accepted_extra["digest"] if accepted_extra else None,
            "approvedTotalMinor":total,"simulatedPaidMinor":simulated_paid,
            "simulatedRefundedMinor":simulated_refunded,"simulatedDueMinor":due,
            "events":events,"outstandingReserves":reserved,
            "unreceivedMaterialCount":unreceived,"executionStatus":execution["status"],
            "blockers":blockers,"canSealPreview":not blockers and doc is None,
            "preview":doc}


def read_demo_close(conn,token,org,loc,order):
    with conn.transaction():
        _authority(conn,token,org,loc,order)
        return _state_snapshot(conn,order)


def simulate_cash(conn,token,org,loc,order,*,request_key,kind,amount_minor):
    if (type(request_key) is not UUID or
            kind not in ("SIM_PAYMENT","SIM_REFUND") or
            type(amount_minor) is not int or not 1<=amount_minor<=10**12):
        raise DemoCloseDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_manager(conn,token,org,loc,order)
        state=_state_snapshot(conn,order)
        existing=conn.execute("""
           SELECT id,actor_id,kind,amount_minor,revision_id
           FROM auto_order_demo_cash_event WHERE request_key=%s
        """,(request_key,)).fetchone()
        if existing:
            if ((existing[1],existing[2],existing[3])!=(actor,kind,amount_minor)
                    or str(existing[4])!=state["approvedRevisionId"]):
                raise DemoCloseConflict("KEY_REUSED")
            return {"ok":True,"replayed":True,**state}
        if state["preview"] is not None:
            raise DemoCloseConflict("PREVIEW_SEALED")
        if state["executionStatus"]!="QC_ACCEPTED" or state["approvedRevisionId"] is None:
            raise DemoCloseConflict("QC_OR_APPROVAL_REQUIRED")
        total=state["approvedTotalMinor"]
        net=state["simulatedPaidMinor"]-state["simulatedRefundedMinor"]
        if (kind=="SIM_PAYMENT" and net+amount_minor>total) or (
                kind=="SIM_REFUND" and amount_minor>net):
            raise DemoCloseConflict("AMOUNT_EXCEEDS_BOUND")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_order_demo_cash_event(
                    id,work_order_id,revision_id,actor_id,request_key,kind,amount_minor)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),order,UUID(state["approvedRevisionId"]),actor,
                     request_key,kind,amount_minor))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise DemoCloseConflict("CONFLICT") from None
        return {"ok":True,"replayed":False,**_state_snapshot(conn,order)}


def release_unused_reserve(conn,token,org,loc,order,*,sku_id,quantity,request_key):
    if (type(sku_id) is not UUID or type(request_key) is not UUID or
            type(quantity) is not int or not 1<=quantity<=1_000_000_000):
        raise DemoCloseDenied("INVALID_INPUT")
    with conn.transaction():
        _warehouse(conn,token,org,loc,order)
        state=_state_snapshot(conn,order)
        if state["preview"] is not None:
            raise DemoCloseConflict("PREVIEW_SEALED")
        if state["executionStatus"] not in ("READY_FOR_QC","QC_ACCEPTED"):
            raise DemoCloseConflict("EXECUTION_NOT_FINISHED")
        try:
            event=mutate_work_order_stock(conn,token,org,loc,order,sku_id,
                                         request_key,"RELEASE",quantity)
        except AutoStockDenied:
            raise DemoCloseDenied("FORBIDDEN") from None
        except AutoStockConflict:
            raise DemoCloseConflict("RESERVE_CONFLICT") from None
        return {"ok":True,"eventId":str(event),**_state_snapshot(conn,order)}


def seal_handover_preview(conn,token,org,loc,order,*,request_key):
    if type(request_key) is not UUID:
        raise DemoCloseDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_manager(conn,token,org,loc,order,cap="CLOSE")
        current=_state_snapshot(conn,order)
        found=conn.execute("""
          SELECT actor_id,request_key FROM auto_order_demo_handover_preview
          WHERE work_order_id=%s
        """,(order,)).fetchone()
        if found:
            if found!=(actor,request_key):
                raise DemoCloseConflict("PREVIEW_ALREADY_SEALED")
            return {"ok":True,"replayed":True,**current}
        if current["blockers"]:
            raise DemoCloseConflict("PREVIEW_BLOCKED")
        estimate=_estimate_state(conn,order)["items"][-1]
        snapshot={
          "kind":"SYNTHETIC_PRE_HANDOVER_PREVIEW",
          "workOrderId":str(order),"estimateRevisionId":estimate["id"],
          "estimateVersion":estimate["version"],"estimateDigest":estimate["digest"],
          "currency":"RUB","lines":estimate["lines"],
          "approvedBaseMinor":current["approvedBaseMinor"],
          "approvedExtraMinor":current["approvedExtraMinor"],
          "extraRevisionId":current["approvedExtraRevisionId"],
          "extraVersion":current["approvedExtraVersion"],
          "extraDigest":current["approvedExtraDigest"],
          "extraLines":(next((item["lines"] for item in _extra_state(conn,order)["items"]
                             if item["id"]==current["approvedExtraRevisionId"]),[])),
          "approvedTotalMinor":current["approvedTotalMinor"],
          "simulatedPaidMinor":current["simulatedPaidMinor"],
          "simulatedRefundedMinor":current["simulatedRefundedMinor"],
          "simulatedDueMinor":current["simulatedDueMinor"],
          "independentQc":"PASS","stockReservations":0,"unreceivedMaterials":0,
          "legalGatePassed":False,"releaseAllowed":False,
        }
        encoded=json.dumps(snapshot,sort_keys=True,separators=(",",":"),
                           ensure_ascii=True)
        # PostgreSQL хранит JSONB в нормализованной форме; отпечаток
        # считается именно от неё и повторно сверяется SQL-триггером.
        fingerprint=conn.execute("""
          SELECT encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')
        """,(encoded,)).fetchone()[0]
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_order_demo_handover_preview
                  (id,work_order_id,revision_id,actor_id,request_key,fingerprint,snapshot)
                  VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)
                """,(uuid4(),order,UUID(estimate["id"]),actor,request_key,
                     fingerprint,encoded))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise DemoCloseConflict("PREVIEW_CONFLICT") from None
        return {"ok":True,"replayed":False,**_state_snapshot(conn,order)}
