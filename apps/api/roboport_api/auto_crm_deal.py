"""Scoped synthetic CRM deal projection from primary booking and order evidence."""
from uuid import UUID

from .auto_crm_leads import _authority, _lead, _state, LeadConflict
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_handover_policy import require_handover_projection
from .auto_order_workspace import read_order_workspace
from .auto_order_customer_read import require_order_staff_scope
from .auto_stock_service import AutoStockDenied
from .auto_treasury import _price

ROLES=("INTAKE","MANAGER","MECHANIC","WAREHOUSE","CASHIER")


def _financial(conn,org,loc,order):
    # Exactly the same approved price as the treasury service. The two
    # journals are independent simulations; never add their balances.
    _,total=_price(conn,order)
    demo=conn.execute("""
      SELECT id,kind,amount_minor,created_at FROM auto_order_demo_cash_event
      WHERE work_order_id=%s ORDER BY created_at,id LIMIT 101
    """,(order,)).fetchall()
    treasury=conn.execute("""
      SELECT e.id,e.kind,e.amount_minor,e.created_at
      FROM auto_treasury_event e JOIN auto_treasury_account a ON a.id=e.account_id
      WHERE e.work_order_id=%s AND a.organization_id=%s AND a.location_id=%s
        AND a.kind IN ('CASH_SIM','BANK_SIM')
      ORDER BY e.created_at,e.id LIMIT 101
    """,(order,org,loc)).fetchall()
    if len(demo)>100 or len(treasury)>100:
        raise LeadConflict("EVENT_LIMIT")
    def sum_kind(rows,*kinds):
        return sum(row[2] for row in rows if row[1] in kinds)
    def events(rows):
        return [{"id":str(i),"kind":k,"amountMinor":str(a),"createdAt":t.isoformat()}
                for i,k,a,t in rows]
    paid=sum_kind(demo,"SIM_PAYMENT")
    refunded=sum_kind(demo,"SIM_REFUND")
    advanced=sum_kind(treasury,"ADVANCE")
    payment=sum_kind(treasury,"PAYMENT")
    applied=sum_kind(treasury,"APPLY_ADVANCE")
    advance_refund=sum_kind(treasury,"REFUND_ADVANCE")
    payment_refund=sum_kind(treasury,"REFUND_PAYMENT")
    settled=payment+applied-payment_refund
    return {"currency":"RUB","approvedTotalMinor":str(total) if total is not None else None,
            "demoClose":{"paidMinor":str(paid),"refundedMinor":str(refunded),
                "dueMinor":str(total-paid+refunded) if total is not None else None,
                "events":events(demo)},
            "treasury":{"advancedMinor":str(advanced),"paymentMinor":str(payment),
                "appliedMinor":str(applied),"refundedAdvanceMinor":str(advance_refund),
                "refundedPaymentMinor":str(payment_refund),"settledMinor":str(settled),
                "dueMinor":str(total-settled) if total is not None else None,
                "events":events(treasury)}}


def read_deal(conn,token,org,loc,ident):
    with conn.transaction():
        # READ COMMITTED is required: assignment/policy evidence is append-only.
        # A repeatable-read snapshot taken before waiting for the lead/tenant
        # lock could authorize a former owner or see an outdated rule.
        # _lead rereads assignment after locking; hold source rows through read.
        actor,manager=_authority(conn,token,org,loc)
        lead=_lead(conn,org,loc,ident,actor,manager)
        state=_state(conn,lead)
        result={"syntheticOnly":True,"realMoney":False,"releaseAllowed":False,
                "leadId":str(ident),"visitNumber":state["visitNumber"],"stage":state["status"],
                "lineage":"NOT_BOOKED","booking":None,"workOrder":None,
                "financeStatus":"UNAVAILABLE","finance":None}
        if state["status"]!="BOOKED":
            return result
        appointment=UUID(state["events"][-1]["appointmentId"])
        # The original booking is immutable. Resolve its actual current
        # resource/time from the latest reschedule, not the old receipt.
        # Resolve reschedule only AFTER locking the appointment. Reschedule
        # holds this row FOR UPDATE and inserts an immutable replacement.
        if not conn.execute("""
          SELECT id FROM auto_appointment WHERE id=%s AND organization_id=%s
            AND location_id=%s AND customer_link_id=%s FOR SHARE
        """,(appointment,org,loc,lead[1])).fetchone():
            result["lineage"]="UNAVAILABLE"
            return result
        row=conn.execute("""
          SELECT b.starts_at,b.ends_at,b.canceled_at,b.id<>a.booking_id
          FROM auto_appointment a
          JOIN auto_bay_booking b ON b.id=COALESCE((
            SELECT moved.new_booking_id FROM auto_appointment_reschedule moved
            WHERE moved.appointment_id=a.id ORDER BY moved.created_at DESC,moved.id DESC LIMIT 1
          ),a.booking_id)
          WHERE a.id=%s AND a.organization_id=%s AND a.location_id=%s
            AND a.customer_link_id=%s AND b.organization_id=a.organization_id
            AND b.location_id=a.location_id
          FOR SHARE OF a,b
        """,(appointment,org,loc,lead[1])).fetchone()
        if row is None:
            result["lineage"]="UNAVAILABLE"
            return result
        start,end,canceled,moved=row
        result["booking"]={"appointmentId":str(appointment),"startsAt":start.isoformat(),
                           "endsAt":end.isoformat(),"status":"CANCELED" if canceled else
                           "RESCHEDULED" if moved else "BOOKED"}
        orders=conn.execute("""
          SELECT id FROM auto_work_order WHERE appointment_id=%s
            AND organization_id=%s AND location_id=%s LIMIT 2
        """,(appointment,org,loc)).fetchall()
        matches=conn.execute("""
          SELECT lead_id FROM auto_crm_lead_stage WHERE stage_code='BOOKED'
            AND appointment_id=%s LIMIT 2
        """,(appointment,)).fetchall()
        if len(matches)!=1 or matches[0][0]!=ident or len(orders)>1:
            result["lineage"]="AMBIGUOUS"
            return result
        if not orders:
            result["lineage"]="NO_ORDER"
            return result
        order=orders[0][0]
        # All primary order mutations serialize on the WorkOrder. Acquire
        # its shared lock before deriving document and monetary projections.
        conn.execute("""SELECT id FROM auto_work_order WHERE id=%s
                         AND organization_id=%s AND location_id=%s FOR SHARE""",
                     (order,org,loc))
        try:
            # All source policies apply to operational projection, not just
            # the CRM role. No order identifier on denied access.
            for document in ("INTAKE_ACT","ESTIMATE","SUPPLIER_ORDER"):
                require_document_access(conn,token,org,loc,ROLES,document,"READ",order=order)
            require_order_staff_scope(conn,token,org,loc,order)
            workspace=read_order_workspace(conn,token,org,loc,order)
        except (DocumentPolicyDenied,AutoStockDenied):
            result["lineage"]="RESTRICTED"
            result["financeStatus"]="RESTRICTED"
            return result
        # The close status itself is protected by all handover sources.
        try:
            require_handover_projection(conn,token,org,loc,order)
            handover=True
        except DocumentPolicyDenied:
            handover=False
        steps=dict(workspace["steps"])
        if not handover:
            steps["technicalDocuments"]="RESTRICTED"
        result["lineage"]="LINKED"
        documents=None
        if handover:
            rows=conn.execute("""
              SELECT id,action,created_at FROM auto_order_demo_document_event
              WHERE work_order_id=%s ORDER BY created_at,id LIMIT 5
            """,(order,)).fetchall()
            if len(rows)>4:
                raise LeadConflict("DOCUMENT_EVENT_LIMIT")
            documents=[{"id":str(i),"action":action,"createdAt":at.isoformat()}
                       for i,action,at in rows]
        result["workOrder"]={"workOrderId":str(order),"steps":steps,
                             "documentEvents":documents}
        if not manager or not handover:
            result["financeStatus"]="RESTRICTED"
            return result
        result["finance"]=_financial(conn,org,loc,order)
        result["financeStatus"]="VISIBLE"
        return result
