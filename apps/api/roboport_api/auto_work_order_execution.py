"""Approved synthetic automobile execution, labor, stock issue/handoff and QC readiness."""
from uuid import UUID,uuid4
import psycopg
from .auto_workshop_qc import _actor_and_order, _snapshot, InspectionDenied
from .auto_work_order_estimate import _estimate_state
from .auto_work_order_additional import _state as _extra_state
from .auto_stock_lot_work_order_issue import issue_work_order_from_lot
from .auto_stock_mechanic_handoff import acknowledge_mechanic_handoff
from .auto_stock_service import AutoStockDenied,AutoStockConflict
from .auto_org_authority import organization_role_allows
from .auto_document_access import has_document_access


class ExecutionDenied(ValueError):
    pass


class ExecutionConflict(ValueError):
    pass


def _scope(conn,token,org,loc,order,roles):
    try:
        return _actor_and_order(conn,token,org,loc,order,roles)
    except InspectionDenied:
        raise ExecutionDenied("FORBIDDEN") from None


def _state(conn,order):
    estimate=_estimate_state(conn,order)
    revisions=estimate["items"]
    approved=revisions[-1] if revisions and revisions[-1]["decision"]=="APPROVED" else None
    extra=_extra_state(conn,order)
    current_extra=extra["items"][-1] if extra["items"] else None
    approved_extra=current_extra if current_extra and current_extra["decision"]=="APPROVED" else None
    additional_pending=bool(current_extra and current_extra["decision"] is None)
    row=conn.execute("""
      SELECT id,revision_id,created_at FROM auto_work_order_execution
      WHERE work_order_id=%s
    """,(order,)).fetchone()
    labor=[]
    if row:
        lines=conn.execute("""
           SELECT service_id,actual_minutes,created_at FROM auto_work_order_labor_entry
           WHERE execution_id=%s ORDER BY created_at,id LIMIT 71
        """,(row[0],)).fetchall()
        if len(lines)>70:
            raise ExecutionConflict("LIMIT")
        labor=[{"serviceId":str(s),"actualMinutes":m,"createdAt":at.isoformat()}
               for s,m,at in lines]
    material=conn.execute("""
       SELECT i.id,i.lot_id,i.sku_id,i.quantity,
              h.id IS NOT NULL AS received
       FROM auto_stock_lot_work_order_issue i
       LEFT JOIN auto_stock_mechanic_handoff h ON h.issue_id=i.id
       WHERE i.work_order_id=%s ORDER BY i.created_at,i.id LIMIT 101
    """,(order,)).fetchall()
    if len(material)>100:
        raise ExecutionConflict("LIMIT")
    materials=[{"issueId":str(i),"lotId":str(l),"skuId":str(s),
                "quantity":q,"received":received}
               for i,l,s,q,received in material]
    finish=conn.execute("""
      SELECT f.created_at FROM auto_work_order_execution_finish f
      JOIN auto_work_order_execution e ON e.id=f.execution_id
      WHERE e.work_order_id=%s
    """,(order,)).fetchone()
    qc=_snapshot(conn,order)
    passed_after_finish=bool(finish and qc["status"]=="QC_PASSED" and
         any(v["result"]=="PASS" and v["createdAt"]>=finish[0].isoformat()
             for v in qc["qcEvents"]))
    state=("NOT_APPROVED" if approved is None else
           "APPROVED" if row is None else
           "READY_FOR_QC" if finish else "IN_PROGRESS")
    if finish and passed_after_finish:
        state="QC_ACCEPTED"
    return {"status":state,"approvedVersion":approved["version"] if approved else None,
            "approvedTotalMinor":approved["totalMinor"] if approved else None,
            "approvedExtraVersion":approved_extra["version"] if approved_extra else None,
            "approvedExtraTotalMinor":approved_extra["totalMinor"] if approved_extra else None,
            "additionalConsentPending":additional_pending,
            "requiredServices":[{"serviceId":line["serviceId"],"code":line["code"],
                   "title":line["title"],"durationMinutes":line["durationMinutes"]}
                  for line in (approved["lines"]+(approved_extra["lines"] if approved_extra else []))] if approved else [],
            "labor":labor,"materials":materials,"qcStatus":qc["status"],
            "executionId":str(row[0]) if row else None,
            "releaseAllowed":False,"syntheticOnly":True}


def _present_state(conn,token,org,loc,order):
    """Only a live MANAGER with unrestricted ESTIMATE sales-price ACL sees
    billable totals. MECHANIC/WAREHOUSE still see work quantities and QC.
    This applies to every response, including idempotent mutation replays.
    """
    result=_state(conn,order)
    price_visible=organization_role_allows(conn,token,org,"MANAGER",loc,
                                           for_mutation=True)
    if price_visible:
        price_visible=all(has_document_access(
            conn,token,org,loc,("MANAGER","MECHANIC","WAREHOUSE"),
            "ESTIMATE",cap,order=order) for cap in ("READ","PRICE_SALES"))
    if not price_visible:
        result["approvedTotalMinor"]=None
        result["approvedExtraTotalMinor"]=None
    return result


def read_execution(conn,token,org,loc,order):
    with conn.transaction():
        _scope(conn,token,org,loc,order,("MANAGER","MECHANIC","WAREHOUSE"))
        return _present_state(conn,token,org,loc,order)


def start_execution(conn,token,org,loc,order,request_key):
    if type(request_key) is not UUID:
        raise ExecutionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,("MANAGER",))
        state=_state(conn,order)
        prior=conn.execute("""
          SELECT id,request_key,actor_id FROM auto_work_order_execution
          WHERE work_order_id=%s
        """,(order,)).fetchone()
        if prior:
            if (prior[1],prior[2])!=(request_key,actor):
                raise ExecutionConflict("ALREADY_STARTED")
            return {"ok":True,"replayed":True,**_present_state(conn,token,org,loc,order)}
        if state["approvedVersion"] is None:
            raise ExecutionConflict("APPROVAL_REQUIRED")
        if _snapshot(conn,order)["inspection"] is None:
            raise ExecutionConflict("INSPECTION_REQUIRED")
        if state["qcStatus"]=="QC_PASSED":
            raise ExecutionConflict("QC_ALREADY_PASSED")
        revision=conn.execute("""
          SELECT r.id FROM auto_work_order_estimate_revision r
          JOIN auto_work_order_estimate_decision d ON d.revision_id=r.id
            AND d.decision='APPROVED'
          WHERE r.work_order_id=%s ORDER BY r.version DESC LIMIT 1
        """,(order,)).fetchone()
        if not revision:
            raise ExecutionConflict("APPROVAL_REQUIRED")
        conn.execute("""
          INSERT INTO auto_work_order_execution
          (id,work_order_id,revision_id,organization_id,location_id,actor_id,request_key)
          VALUES(%s,%s,%s,%s,%s,%s,%s)
        """,(uuid4(),order,revision[0],org,loc,actor,request_key))
        return {"ok":True,"replayed":False,**_present_state(conn,token,org,loc,order)}


def labor_execution(conn,token,org,loc,order,service_id,minutes,request_key):
    if (type(service_id) is not UUID or type(request_key) is not UUID or
        type(minutes) is not int or not 1<=minutes<=1440):
        raise ExecutionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,("MECHANIC",))
        execution_row=conn.execute("""
           SELECT id FROM auto_work_order_execution WHERE work_order_id=%s
        """,(order,)).fetchone()
        # A lost 201 can be safely replayed even after finish/QC. Verify the
        # original actor, order, service and payload before checking workflow
        # status; never create a fresh entry after the execution is closed.
        prior=conn.execute("""
           SELECT execution_id,service_id,actual_minutes,actor_id,request_key
           FROM auto_work_order_labor_entry WHERE request_key=%s
        """,(request_key,)).fetchone()
        if prior:
            if (execution_row is None or
                    prior!=(execution_row[0],service_id,minutes,actor,request_key)):
                raise ExecutionConflict("LABOR_IMMUTABLE")
            return {"ok":True,"replayed":True,**_present_state(conn,token,org,loc,order)}
        state=_state(conn,order)
        if state["status"]!="IN_PROGRESS":
            raise ExecutionConflict("NOT_IN_PROGRESS")
        if str(service_id) not in [v["serviceId"] for v in state["requiredServices"]]:
            raise ExecutionConflict("SERVICE_NOT_APPROVED")
        _require_assigned_mechanic(conn,order,actor)
        execution=execution_row[0]
        existing=conn.execute("""
           SELECT 1 FROM auto_work_order_labor_entry
           WHERE execution_id=%s AND service_id=%s
        """,(execution,service_id)).fetchone()
        if existing:
            raise ExecutionConflict("LABOR_IMMUTABLE")
        conn.execute("""
          INSERT INTO auto_work_order_labor_entry
          (id,execution_id,service_id,actor_id,actual_minutes,request_key)
          VALUES(%s,%s,%s,%s,%s,%s)
        """,(uuid4(),execution,service_id,actor,minutes,request_key))
        return {"ok":True,"replayed":False,**_present_state(conn,token,org,loc,order)}


def issue_material(conn,token,org,loc,order,lot_id,quantity,request_key):
    if type(lot_id) is not UUID or type(request_key) is not UUID or type(quantity) is not int or not 1<=quantity<=1_000_000_000:
        raise ExecutionDenied("INVALID_INPUT")
    with conn.transaction():
        _scope(conn,token,org,loc,order,("WAREHOUSE",))
        prior=conn.execute("""
           SELECT id,work_order_id,lot_id,quantity
           FROM auto_stock_lot_work_order_issue WHERE request_id=%s
        """,(request_key,)).fetchone()
        if prior:
            if (prior[1],prior[2],prior[3])!=(order,lot_id,quantity):
                raise ExecutionConflict("STOCK_CONFLICT")
            return {"ok":True,"replayed":True,"issueId":str(prior[0]),
                    **_present_state(conn,token,org,loc,order)}
        if _state(conn,order)["status"]!="IN_PROGRESS":
            raise ExecutionConflict("NOT_IN_PROGRESS")
        try:
            issue=issue_work_order_from_lot(conn,token,org,loc,order,lot_id,
                                            request_key,quantity)
        except AutoStockDenied:
            raise ExecutionDenied("FORBIDDEN") from None
        except AutoStockConflict:
            raise ExecutionConflict("STOCK_CONFLICT") from None
        return {"ok":True,"replayed":False,"issueId":str(issue),
                **_present_state(conn,token,org,loc,order)}


def _require_assigned_mechanic(conn,order,actor):
    """An accepted offer owns execution handoff/finish, not just labor."""
    row=conn.execute("""
      SELECT mechanic_id,status FROM auto_work_order_assignment
      WHERE work_order_id=%s ORDER BY created_at DESC,id DESC LIMIT 1
    """,(order,)).fetchone()
    if row is not None:
        if row[1]!="ACCEPTED":
            raise ExecutionConflict("ASSIGNMENT_NOT_ACCEPTED")
        if row[0]!=actor:
            raise ExecutionDenied("FORBIDDEN")


def receive_material(conn,token,org,loc,order,issue_id,request_key):
    if type(issue_id) is not UUID or type(request_key) is not UUID:
        raise ExecutionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,("MECHANIC",))
        match=conn.execute("""
           SELECT id FROM auto_stock_lot_work_order_issue
           WHERE id=%s AND work_order_id=%s
        """,(issue_id,order)).fetchone()
        if match is None:
            raise ExecutionDenied("FORBIDDEN")
        prior=conn.execute("""
           SELECT id,issue_id,received_by FROM auto_stock_mechanic_handoff
           WHERE request_id=%s
        """,(request_key,)).fetchone()
        if prior:
            if (prior[1],prior[2])!=(issue_id,actor):
                raise ExecutionConflict("HANDOFF_CONFLICT")
            return {"ok":True,"replayed":True,"handoffId":str(prior[0]),
                    **_present_state(conn,token,org,loc,order)}
        if _state(conn,order)["status"]!="IN_PROGRESS":
            raise ExecutionConflict("NOT_IN_PROGRESS")
        _require_assigned_mechanic(conn,order,actor)
        try:
            receipt=acknowledge_mechanic_handoff(conn,token,org,loc,issue_id,request_key)
        except AutoStockDenied:
            raise ExecutionDenied("FORBIDDEN") from None
        except AutoStockConflict:
            raise ExecutionConflict("HANDOFF_CONFLICT") from None
        return {"ok":True,"replayed":False,"handoffId":str(receipt),
                **_present_state(conn,token,org,loc,order)}


def finish_execution(conn,token,org,loc,order,request_key):
    if type(request_key) is not UUID:
        raise ExecutionDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,("MECHANIC",))
        state=_state(conn,order)
        execution=conn.execute("""
          SELECT id FROM auto_work_order_execution WHERE work_order_id=%s
        """,(order,)).fetchone()
        if execution is None:
            raise ExecutionConflict("NOT_STARTED")
        previous=conn.execute("""
          SELECT actor_id,request_key FROM auto_work_order_execution_finish
          WHERE execution_id=%s
        """,(execution[0],)).fetchone()
        if previous:
            if previous!=(actor,request_key):
                raise ExecutionConflict("ALREADY_FINISHED")
            return {"ok":True,"replayed":True,**_present_state(conn,token,org,loc,order)}
        if state["status"]!="IN_PROGRESS":
            raise ExecutionConflict("NOT_IN_PROGRESS")
        _require_assigned_mechanic(conn,order,actor)
        if state["additionalConsentPending"]:
            raise ExecutionConflict("ADDITIONAL_CONSENT_PENDING")
        if {v["serviceId"] for v in state["labor"]}!={v["serviceId"] for v in state["requiredServices"]}:
            raise ExecutionConflict("LABOR_INCOMPLETE")
        if any(not v["received"] for v in state["materials"]):
            raise ExecutionConflict("MATERIAL_HANDOFF_INCOMPLETE")
        required=conn.execute("""
          SELECT component.sku_id,sum(component.quantity) AS required_qty
          FROM auto_work_order_complex_selection chosen
          JOIN auto_work_complex_material component
            ON component.complex_id=chosen.complex_id
          WHERE chosen.work_order_id=%s
          GROUP BY component.sku_id
        """,(order,)).fetchall()
        issued={}
        for item in state["materials"]:
            issued[item["skuId"]]=issued.get(item["skuId"],0)+item["quantity"]
        # A confirmed handoff is immutable audit evidence, not proof that
        # material was retained: warehouse returns credit the original lot.
        for sku,quantity in conn.execute("""
          SELECT sku_id,SUM(quantity) FROM auto_stock_lot_work_order_return
          WHERE work_order_id=%s GROUP BY sku_id
        """,(order,)).fetchall():
            issued[str(sku)]=issued.get(str(sku),0)-quantity
        if any(issued.get(str(sku),0)<need for sku,need in required):
            raise ExecutionConflict("COMPLEX_MATERIAL_ISSUE_INCOMPLETE")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_work_order_execution_finish
                    (id,execution_id,actor_id,request_key)
                  VALUES(%s,%s,%s,%s)
                """,(uuid4(),execution[0],actor,request_key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            raise ExecutionConflict("EXECUTION_INTEGRITY_CONFLICT") from None
        return {"ok":True,"replayed":False,**_present_state(conn,token,org,loc,order)}
