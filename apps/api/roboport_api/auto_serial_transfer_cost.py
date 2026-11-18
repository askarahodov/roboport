"""Synthetic serial receipt-cost continuity across real interbranch transfers.

Every selected lot allocation must be matched to a physical serial transition.
Prices come only from the original accepted/approved receipt, with live
manager and purchase-price rights in the *source* location. Never infer
origin from a destination TRANSFER_IN valuation or a serial number.
"""
from __future__ import annotations

from collections import defaultdict

from .auto_closed_order_report import Conflict, Denied
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_org_authority import organization_role_allows

MAX_SERIALS = 5000
MAX_SERIAL_STEPS = 5000


def _origin_rights(conn, token, org, loc):
    if not organization_role_allows(conn, token, org, "MANAGER", loc,
                                   for_mutation=True):
        raise Denied()
    try:
        for kind, roles, cap in (
            ("SUPPLIER_ORDER", ("MANAGER", "WAREHOUSE"), "READ"),
            ("SUPPLIER_ORDER", ("MANAGER", "WAREHOUSE"), "PRICE_PURCHASE"),
            ("FINANCE", ("MANAGER", "CASHIER"), "PRICE_PURCHASE"),
        ):
            if require_document_access(conn, token, org, loc, roles, kind, cap) is not None:
                raise Denied()
    except DocumentPolicyDenied:
        raise Denied() from None


def prove(conn, token, org, loc, lot, history, *, limit=MAX_SERIAL_STEPS):
    """Return status, movement slice map, globally scanned serial transitions.

    No persisted costing mutations. Bad/missing historic links fail closed.
    Price scope failures raise Denied, not an incomplete report with prices.
    """
    serials=conn.execute("""
      SELECT serial_id FROM auto_stock_serial_movement
        WHERE source_lot_id=%s OR destination_lot_id=%s
      UNION
      SELECT id FROM auto_stock_serial WHERE lot_id=%s
      LIMIT %s
    """,(lot,lot,lot,MAX_SERIALS+1)).fetchall()
    if len(serials)>MAX_SERIALS:
        raise Conflict()
    ids=[r[0] for r in serials]
    steps=conn.execute("""
      SELECT m.id,m.serial_id,m.action,m.source_lot_id,m.destination_lot_id,
             m.work_order_id,m.issue_id,m.return_id,m.interbranch_id,m.movement_sequence,
             s.lot_id, p.receipt_id,ap.id,p.cost_registration_seq,ap.cost_registration_seq,
             d.organization_id,d.location_id,d.lot_id,d.sku_id,ac.id,
             v.id,v.currency,v.purchase_unit_net_minor,v.cost_registration_seq,
             receipt_allocation.cost_registration_seq,
             t.id,t.source_lot_id,t.destination_lot_id,t.quantity,
             t.debit_allocation_id,t.credit_allocation_id,
             debit.cost_registration_seq,credit.cost_registration_seq,
             i.lot_debit_id,r.lot_credit_id,
             issue_allocation.cost_registration_seq,
             return_allocation.cost_registration_seq,
             i.quantity,r.quantity
      FROM auto_stock_serial_movement m
      JOIN auto_stock_serial s ON s.id=m.serial_id
      LEFT JOIN auto_stock_serial_receipt_assertion p ON p.serial_id=m.serial_id
      LEFT JOIN auto_stock_serial_receipt_approval ap ON ap.assertion_id=p.id
      LEFT JOIN auto_stock_receipt_document d ON d.id=p.receipt_id
      LEFT JOIN auto_stock_receipt_acceptance ac ON ac.document_id=d.id
      LEFT JOIN auto_stock_lot_allocation receipt_allocation
        ON receipt_allocation.id=ac.allocation_event_id
      LEFT JOIN auto_stock_receipt_valuation v ON v.document_id=d.id
      LEFT JOIN auto_stock_interbranch_transfer t ON t.id=m.interbranch_id
      LEFT JOIN auto_stock_lot_allocation debit ON debit.id=t.debit_allocation_id
      LEFT JOIN auto_stock_lot_allocation credit ON credit.id=t.credit_allocation_id
      LEFT JOIN auto_stock_lot_work_order_issue i ON i.id=m.issue_id
      LEFT JOIN auto_stock_lot_allocation issue_allocation ON issue_allocation.id=i.lot_debit_id
      LEFT JOIN auto_stock_lot_work_order_return r ON r.id=m.return_id
      LEFT JOIN auto_stock_lot_allocation return_allocation ON return_allocation.id=r.lot_credit_id
      WHERE m.serial_id=ANY(%s::uuid[])
      ORDER BY m.movement_sequence LIMIT %s
    """,(ids,limit+1)).fetchall()
    if len(steps)>limit:
        raise Conflict()
    physical={}
    for row in history:
        (_,allocation,action,qty,receipt,_,_,_,_,_,_,issue,returned,
         legs,valid,seq,order)=row
        if seq is None or type(qty) is not int or not 1<=qty<=10**9:
            return "UNPROVEN",{},len(steps)
        if receipt is not None:
            if legs!=1 or valid is not True or action!="ALLOCATE":
                return "UNPROVEN",{},len(steps)
        elif issue is not None or returned is not None:
            if legs!=1 or valid is not True or qty!=1 or order is None:
                return "UNPROVEN",{},len(steps)
            physical[allocation]=(action,issue or returned,order)
        else:
            if qty!=1:
                return "UNPROVEN",{},len(steps)
            physical[allocation]=(action,None,None)
    used=set()
    sources={}
    current={}
    outstanding={}
    parts={}
    for row in steps:
        (movement_id,serial,action,src,dst,order,issue,returned,transfer,serial_seq,
         original_lot,receipt,approval,assertion_seq,approval_seq,
         source_org,source_loc,receipt_lot,receipt_sku,acceptance,
         valuation,currency,unit,value_seq,receive_seq,
         transfer_id,transfer_src,transfer_dst,transfer_qty,debit_id,credit_id,
         debit_seq,credit_seq,issue_alloc,return_alloc,issue_seq,return_seq,
         issue_qty,return_qty)=row
        if serial not in current:
            current[serial]=original_lot
        if current[serial]!=src or serial_seq is None:
            return "UNPROVEN",{},len(steps)
        if (source_org!=org or receipt is None or approval is None or
            receipt_lot!=original_lot or acceptance is None or receive_seq is None or
            assertion_seq is None or approval_seq is None or
            not receive_seq<assertion_seq<approval_seq):
            return "UNPROVEN",{},len(steps)
        if source_loc!=loc and source_loc not in sources:
            _origin_rights(conn,token,org,source_loc)
            sources[source_loc]=True
        if valuation is None:
            return "UNVALUED",{},len(steps)
        if currency!="RUB":
            return "OTHER_CURRENCY",{},len(steps)
        if type(unit) is not int or not 0<=unit<=10**9:
            raise Conflict()
        if value_seq is None or value_seq<=receive_seq:
            return "UNPROVEN",{},len(steps)
        if action=="TRANSFER":
            seq=debit_seq
        elif action=="ISSUE":
            seq=issue_seq
        elif action=="RETURN":
            seq=return_seq
        else:
            return "UNPROVEN",{},len(steps)
        if seq is None or approval_seq>=seq:
            return "UNPROVEN",{},len(steps)
        if value_seq>=seq:
            return "LATE_VALUATION",{},len(steps)
        if action=="TRANSFER":
            if (serial in outstanding or transfer_id!=transfer or transfer_qty!=1 or
                transfer_src!=src or transfer_dst!=dst or dst is None or
                debit_id is None or credit_id is None or debit_seq is None or credit_seq is None):
                return "UNPROVEN",{},len(steps)
            for target,alloc,expected in ((src,debit_id,"DEALLOCATE"),
                                          (dst,credit_id,"ALLOCATE")):
                if target==lot:
                    if physical.get(alloc)!=(expected,None,None) or alloc in used:
                        return "UNPROVEN",{},len(steps)
                    used.add(alloc)
            current[serial]=dst
            continue
        if action=="ISSUE":
            if (serial in outstanding or issue is None or issue_qty!=1 or
                issue_alloc is None or returned is not None):
                return "UNPROVEN",{},len(steps)
            outstanding[serial]=(issue,order,src)
            origin=issue
            allocation=issue_alloc
            expected="DEALLOCATE"
            movement=issue
        else:
            prior=outstanding.pop(serial,None)
            if (prior is None or prior[1:]!=(order,src) or returned is None or
                return_qty!=1 or return_alloc is None or issue is not None):
                return "UNPROVEN",{},len(steps)
            origin=prior[0]
            allocation=return_alloc
            expected="ALLOCATE"
            movement=returned
        if src==lot:
            if (physical.get(allocation)!=(expected,movement,order) or
                allocation in used or movement in parts):
                return "UNPROVEN",{},len(steps)
            used.add(allocation)
            parts[movement]=[{
                "originalIssueId":str(origin),"quantity":1,
                "receiptId":str(receipt),"acceptanceId":str(acceptance),
                "valuationId":str(valuation),"unitNetMinor":unit,
                "serialId":str(serial),"serialApprovalId":str(approval),
                "sourceLocationId":str(source_loc),
            }]
    if used!=set(physical):
        return "UNPROVEN",{},len(steps)
    if not parts:
        return "UNPROVEN",{},len(steps)
    return "SERIAL_TRACED",parts,len(steps)
