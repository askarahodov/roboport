"""Verify serialized stock cost from independent immutable receipt evidence.

A serial can be returned/reissued or transferred; its receipt value is retained.
Unknown registration/approval/chronology/physical pairing never produces a cost.
"""
from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from .auto_closed_order_report import Conflict, Denied, MAX_SAFE
from .auto_document_access import require_document_access, DocumentPolicyDenied
from .auto_org_authority import organization_role_allows

MAX_SERIAL_MOVEMENTS=5000
STATUS="SERIAL_TRACED"


def prove_lot(conn,token,org,loc,lot,history,*,budget=MAX_SERIAL_MOVEMENTS):
    # Rows: lot,allocation,action,quantity,receipt,acceptance,valuation,
    # currency,unit,valuation_seq,receipt_seq,issue,return,leg_count,valid,
    # allocation_seq,work_order.
    mrows=conn.execute("""
      SELECT m.id,m.serial_id,m.action,m.source_lot_id,m.destination_lot_id,
             m.work_order_id,m.issue_id,m.return_id,m.interbranch_id,
             m.movement_sequence,m.cost_registration_seq,
             a.receipt_id,ap.id,d.organization_id,d.location_id,d.lot_id,
             d.sku_id,ac.id,v.id,v.currency,v.purchase_unit_net_minor,
             v.cost_registration_seq,
             t.debit_allocation_id,t.credit_allocation_id,
             m.performed_by
      FROM auto_stock_serial_movement m
      JOIN auto_stock_serial s ON s.id=m.serial_id
      LEFT JOIN auto_stock_serial_receipt_assertion a ON a.serial_id=s.id
      LEFT JOIN auto_stock_serial_receipt_approval ap ON ap.assertion_id=a.id
      LEFT JOIN auto_stock_receipt_document d ON d.id=a.receipt_id
      LEFT JOIN auto_stock_receipt_acceptance ac ON ac.document_id=d.id
      LEFT JOIN auto_stock_receipt_valuation v ON v.document_id=d.id
      LEFT JOIN auto_stock_interbranch_transfer t ON t.id=m.interbranch_id
      WHERE m.source_lot_id=%s OR m.destination_lot_id=%s
      ORDER BY m.movement_sequence LIMIT %s
    """,(lot,lot,budget+1)).fetchall()
    if len(mrows)>budget:
        raise Conflict()
    if not mrows:
        return "UNPROVEN",{},len(mrows)
    by_issue,by_return,by_transfer={}, {}, {}
    by_serial=defaultdict(list)
    for m in mrows:
        ident,serial,action,src,dst,order,issue,returned,transfer,seq,cseq,receipt,approval,            source_org,source_loc,source_lot,source_sku,acceptance,valuation,currency,unit,            value_seq,debit,credit,actor=m
        if cseq is None or receipt is None or approval is None or acceptance is None:
            return "UNPROVEN",{},len(mrows)
        if source_org!=org:
            return "UNPROVEN",{},len(mrows)
        # The report runs for a destination branch; an interbranch receipt
        # provenance is purchase-sensitive to the ORIGINAL branch.
        if source_loc!=loc:
            if not organization_role_allows(conn,token,org,"MANAGER",source_loc,
                                             for_mutation=True):
                raise Denied()
            try:
                for doc,cap in (("SUPPLIER_ORDER","READ"),
                                ("SUPPLIER_ORDER","PRICE_PURCHASE"),
                                ("FINANCE","PRICE_PURCHASE")):
                    if require_document_access(conn,token,org,source_loc,
                            ("MANAGER","WAREHOUSE") if doc=="SUPPLIER_ORDER"
                            else ("MANAGER","CASHIER"),doc,cap) is not None:
                        raise Denied()
            except DocumentPolicyDenied:
                raise Denied() from None
        if valuation is None:
            return "UNVALUED",{},len(mrows)
        if currency!="RUB":
            return "OTHER_CURRENCY",{},len(mrows)
        if type(unit) is not int or not 0<=unit<=10**9:
            raise Conflict()
        if value_seq is None or value_seq>=cseq:
            return "LATE_VALUATION",{},len(mrows)
        if source_lot is None or source_sku is None:
            return "UNPROVEN",{},len(mrows)
        if action=="ISSUE":
            if issue is None or order is None or issue in by_issue:
                return "UNPROVEN",{},len(mrows)
            by_issue[issue]=m
        elif action=="RETURN":
            if returned is None or order is None or returned in by_return:
                return "UNPROVEN",{},len(mrows)
            by_return[returned]=m
        elif action=="TRANSFER":
            if transfer is None or debit is None or credit is None or transfer in by_transfer:
                return "UNPROVEN",{},len(mrows)
            by_transfer[transfer]=m
        else:
            return "UNPROVEN",{},len(mrows)
        by_serial[serial].append(m)

    # Check even movements of OTHER orders in the same lot, not merely the
    # selected report order. A later duplicate or unpaired debit poisons it.
    allocation_ids=set()
    parts={}
    for row in history:
        _,aid,action,qty,receipt,acceptance,valuation,_,_,_,_,issue,returned,legs,valid,seq,order=row
        if seq is None or type(qty) is not int or qty!=1 and receipt is None:
            return "UNPROVEN",{},len(mrows)
        if receipt is not None:
            if legs!=1 or valid is not True or action!="ALLOCATE":
                return "UNPROVEN",{},len(mrows)
            continue
        if issue is not None or returned is not None:
            if legs!=1 or valid is not True or qty!=1:
                return "UNPROVEN",{},len(mrows)
            movement=by_issue.get(issue) if issue is not None else by_return.get(returned)
            if movement is None or movement[3]!=lot or movement[5]!=order:
                return "UNPROVEN",{},len(mrows)
            allocation_ids.add(aid)
            continue
        # An interbranch transfer requires an exactly paired serial movement.
        transfer=next((m for m in by_transfer.values()
                       if aid in (m[22],m[23]) and lot in (m[3],m[4])),None)
        if transfer is None or qty!=1:
            return "UNPROVEN",{},len(mrows)
        if action!=("DEALLOCATE" if lot==transfer[3] else "ALLOCATE"):
            return "UNPROVEN",{},len(mrows)
        allocation_ids.add(aid)
    if len(allocation_ids)!=sum(r[4] is None for r in history):
        return "UNPROVEN",{},len(mrows)

    for serial,movements in by_serial.items():
        open_issue=None
        for m in sorted(movements,key=lambda x:x[9]):
            _,_,action,src,dst,order,issue,returned,transfer,seq,cseq,receipt,                approval,source_org,source_loc,source_lot,source_sku,acceptance,valuation,                currency,unit,value_seq,debit,credit,actor=m
            if action=="ISSUE":
                if open_issue is not None:
                    return "UNPROVEN",{},len(mrows)
                open_issue=(issue,order,src)
                movement_id=issue
                sign=1
            elif action=="RETURN":
                if open_issue is None or open_issue[1:]!=(order,src):
                    return "UNPROVEN",{},len(mrows)
                movement_id=returned
                sign=-1
            else:
                if open_issue is not None:
                    return "UNPROVEN",{},len(mrows)
                continue
            original_issue=open_issue[0]
            part={
                "serialId":str(serial),"serialMovementId":str(m[0]),
                "originalIssueId":str(original_issue),"receiptId":str(receipt),
                "acceptanceId":str(acceptance),"valuationId":str(valuation),
                "sourceLocationId":str(source_loc),
                "unitNetMinor":unit,"quantity":1,
            }
            if movement_id in parts:
                return "UNPROVEN",{},len(mrows)
            parts[movement_id]=part
            if sign==-1:
                open_issue=None
    if any(r[11] is not None and r[11] not in parts or
           r[12] is not None and r[12] not in parts for r in history):
        return "UNPROVEN",{},len(mrows)
    return STATUS,parts,len(mrows)


def group_slices(events,parts):
    slices=[]
    total=0
    for stock_event,action,qty,movement in events:
        if qty!=1 or movement not in parts:
            raise Conflict()
        p=parts[movement]
        amount=p["unitNetMinor"]
        total+=amount if action=="ISSUE" else -amount
        if not -MAX_SAFE<=total<=MAX_SAFE:
            raise Conflict()
        slices.append({
            "stockEventId":str(stock_event),"lotMovementId":str(movement),
            "action":action,**p,
        })
    if total<0:
        raise Conflict()
    return total,slices
