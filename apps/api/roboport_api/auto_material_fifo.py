"""Bounded deterministic FIFO replay for a non-serialized multi-receipt lot.

Receipt sequence determines priority; a return reverses latest still-open
issue slices for the same work order and restores their original receipt layer.
This is a synthetic valuation convention, not physical serial identification.
"""
from collections import defaultdict

from .auto_closed_order_report import Conflict, MAX_SAFE

MAX_SLICES = 5000


def replay(rows, *, limit=MAX_SLICES):
    """Rows are the validated lot allocation history with order id appended.

    Return (status, slices keyed by movement UUID). Unknown provenance is
    contagious for the lot: never guess the origin of later issues.
    """
    if any(row[13]!=1 or row[14] is not True or row[15] is None for row in rows):
        return "UNPROVEN", {}
    seqs=[row[15] for row in rows]
    if len(set(seqs))!=len(seqs):
        raise Conflict()
    layers=[]
    available={}
    open_issues=defaultdict(list)
    movements={}
    count=0
    for row in sorted(rows,key=lambda r:r[15]):
        (_,allocation,action,qty,receipt,acceptance,valuation,currency,
         unit,value_seq,receipt_seq,issue,returned,_,_,seq,order)=row
        if type(qty) is not int or not 1<=qty<=10**9:
            raise Conflict()
        if receipt is not None:
            if action!="ALLOCATE" or receipt_seq!=seq or receipt in available:
                return "UNPROVEN", {}
            if valuation is None:
                return "UNVALUED", {}
            if value_seq is None or value_seq<=seq:
                return "UNPROVEN", {}
            if currency!="RUB":
                return "OTHER_CURRENCY", {}
            if type(unit) is not int or not 0<=unit<=10**9:
                raise Conflict()
            source={"receiptId":str(receipt),"acceptanceId":str(acceptance),
                    "valuationId":str(valuation),"unitNetMinor":unit}
            layers.append((receipt,source,value_seq))
            available[receipt]=qty
            continue
        if order is None or (issue is None)==(returned is None):
            return "UNPROVEN", {}
        movement=issue or returned
        if movement in movements:
            raise Conflict()
        slices=[]
        outstanding=qty
        if issue is not None and action=="DEALLOCATE":
            for receipt,source,value_seq in layers:
                take=min(outstanding,available[receipt])
                if not take:
                    continue
                if value_seq>=seq:
                    return "LATE_VALUATION", {}
                available[receipt]-=take
                outstanding-=take
                open_issues[order].append((receipt,source,issue,take))
                slices.append((receipt,source,issue,take))
                count+=1
                if count>limit:
                    raise Conflict()
                if not outstanding:
                    break
        elif returned is not None and action=="ALLOCATE":
            stack=open_issues[order]
            while outstanding and stack:
                receipt,source,origin,remaining=stack.pop()
                take=min(outstanding,remaining)
                available[receipt]+=take
                outstanding-=take
                if remaining>take:
                    stack.append((receipt,source,origin,remaining-take))
                slices.append((receipt,source,origin,take))
                count+=1
                if count>limit:
                    raise Conflict()
        else:
            return "UNPROVEN", {}
        if outstanding:
            return "UNPROVEN", {}
        movements[movement]=[
            {"originalIssueId":str(origin),"quantity":qty,**source}
            for _,source,origin,qty in slices]
    return "FIFO_TRACED", movements


def group_slices(events, movements):
    result=[]
    total=0
    for event,action,quantity,movement in events:
        if movement not in movements:
            raise Conflict()
        parts=movements[movement]
        if sum(p["quantity"] for p in parts)!=quantity:
            raise Conflict()
        for p in parts:
            amount=p["quantity"]*p["unitNetMinor"]
            if amount>MAX_SAFE:
                raise Conflict()
            total+=amount if action=="ISSUE" else -amount
            result.append({"stockEventId":str(event),"lotMovementId":str(movement),
                           "action":action,**p})
    if not 0<=total<=MAX_SAFE:
        raise Conflict()
    return total,result
