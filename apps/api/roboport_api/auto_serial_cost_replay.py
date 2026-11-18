"""Synthetic per-serial receipt cost replay for multi-receipt lots.

All physical ISSUE/RETURN movements in a serial lot must be mapped one-to-one
to immutable serial movements, with an approved source prior to use. Any
transfer, unpaired movement, missing source or historic ambiguity fails closed.
No serial numbers, names or client IDs are returned.
"""
from .auto_closed_order_report import Conflict,MAX_SAFE

MAX_SERIAL_EVENTS=5000


def replay(history, serial_rows, *, limit=MAX_SERIAL_EVENTS):
    # History rows: allocation + source + work order, as in auto_material_report.
    if len(history)>limit or len(serial_rows)>limit:
        raise Conflict()
    physical={}
    receipts={}
    for row in history:
        (lot, allocation, action, quantity, receipt, acceptance, valuation,
         currency, unit, value_seq, receive_seq, issue, returned,
         legs,valid,allocation_seq,order)=row
        if legs!=1 or valid is not True or allocation_seq is None:
            return "UNPROVEN",{}
        if type(quantity) is not int or quantity<1 or quantity>10**9:
            raise Conflict()
        if receipt is not None:
            if receipt in receipts or action!="ALLOCATE" or receive_seq!=allocation_seq:
                return "UNPROVEN",{}
            receipts[receipt]=(lot,acceptance,valuation,currency,unit,value_seq,receive_seq)
        else:
            if quantity!=1 or order is None or (issue is None)==(returned is None):
                return "UNPROVEN",{}
            movement=issue or returned
            if movement in physical:
                raise Conflict()
            physical[movement]=(lot,action,order,allocation_seq)
    slices={}
    used=set()
    issued={}
    for row in sorted(serial_rows,key=lambda x:x[7]):
        (source_lot,destination,serial,action,issue,returned,order,serial_sequence,
         receipt,approval,assertion_seq,approval_seq,document_lot,
         acceptance,valuation,value_seq,currency,unit)=row
        if destination is not None or action=="TRANSFER":
            return "UNPROVEN",{}
        movement=issue if action=="ISSUE" else returned if action=="RETURN" else None
        if movement is None or movement not in physical or movement in used:
            return "UNPROVEN",{}
        used.add(movement)
        lot,physical_action,physical_order,allocation_seq=physical[movement]
        if (lot!=source_lot or physical_order!=order or
            physical_action!=("DEALLOCATE" if action=="ISSUE" else "ALLOCATE")):
            return "UNPROVEN",{}
        if (receipt not in receipts or approval is None or assertion_seq is None or
            approval_seq is None or document_lot!=lot):
            return "UNPROVEN",{}
        _,stored_acceptance,stored_valuation,stored_currency,stored_unit,stored_value_seq,receive_seq=receipts[receipt]
        if (acceptance!=stored_acceptance or valuation!=stored_valuation or
            currency!=stored_currency or unit!=stored_unit or value_seq!=stored_value_seq or
            not receive_seq<assertion_seq<approval_seq<allocation_seq):
            return "UNPROVEN",{}
        if valuation is None:
            return "UNVALUED",{}
        if value_seq is None or value_seq<=receive_seq:
            return "UNPROVEN",{}
        if value_seq>=allocation_seq:
            return "LATE_VALUATION",{}
        if currency!="RUB":
            return "OTHER_CURRENCY",{}
        if type(unit) is not int or not 0<=unit<=10**9:
            raise Conflict()
        if action=="ISSUE":
            if serial in issued:
                return "UNPROVEN",{}
            issued[serial]=(movement,order,receipt,unit)
            origin=movement
        else:
            origin_row=issued.pop(serial,None)
            if origin_row is None or origin_row[1:]!=(order,receipt,unit):
                return "UNPROVEN",{}
            origin=origin_row[0]
        slices[movement]=[{
            "originalIssueId":str(origin),"quantity":1,
            "receiptId":str(receipt),"acceptanceId":str(acceptance),
            "valuationId":str(valuation),"unitNetMinor":unit,
            "serialId":str(serial),"serialApprovalId":str(approval),
        }]
    if used!=set(physical):
        return "UNPROVEN",{}
    return "SERIAL_TRACED",slices
