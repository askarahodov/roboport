"""Deterministic replay tests complement the real PostgreSQL lifecycle."""
from uuid import uuid4

import pytest

from roboport_api.auto_material_fifo import replay, group_slices
from roboport_api.auto_closed_order_report import Conflict


def receipt(lot, seq, qty, unit, *, value_seq=None, currency="RUB"):
    return (lot,uuid4(),"ALLOCATE",qty,uuid4(),uuid4(),uuid4(),currency,
            unit,value_seq or seq+1,seq,None,None,1,True,seq,None)


def move(lot,seq,qty,order,action):
    issue=uuid4() if action=="ISSUE" else None
    returned=uuid4() if action=="RETURN" else None
    return (lot,uuid4(),"DEALLOCATE" if issue else "ALLOCATE",qty,
            None,None,None,None,None,None,None,issue,returned,1,True,seq,order)


def test_fifo_interleaved_orders_return_reissue_preserves_receipt_priority():
    lot,order_a,order_b=uuid4(),uuid4(),uuid4()
    a=receipt(lot,1,3,1250)
    b=receipt(lot,3,3,2000)
    i1=move(lot,5,4,order_a,"ISSUE")
    i2=move(lot,6,1,order_b,"ISSUE")
    r1=move(lot,7,2,order_a,"RETURN")
    i3=move(lot,8,2,order_b,"ISSUE")
    status,parts=replay([i3,b,i2,i1,a,r1])
    assert status=="FIFO_TRACED"
    def pairs(movement):
        return [(s["receiptId"],s["quantity"],s["originalIssueId"]) for s in parts[movement]]
    assert pairs(i1[11])==[(str(a[4]),3,str(i1[11])),(str(b[4]),1,str(i1[11]))]
    assert pairs(i2[11])==[(str(b[4]),1,str(i2[11]))]
    assert pairs(r1[12])==[(str(b[4]),1,str(i1[11])),(str(a[4]),1,str(i1[11]))]
    assert pairs(i3[11])==[(str(a[4]),1,str(i3[11])),(str(b[4]),1,str(i3[11]))]
    # Each order's own returns reverse exactly its original portions.
    cost_a,_=group_slices([(uuid4(),"ISSUE",4,i1[11]),(uuid4(),"RETURN",2,r1[12])],parts)
    cost_b,_=group_slices([(uuid4(),"ISSUE",1,i2[11]),(uuid4(),"ISSUE",2,i3[11])],parts)
    assert cost_a==2500 and cost_b==5250
    assert cost_a+cost_b==7750


def test_fifo_late_valuation_and_unproven_cross_order_return():
    lot,a,b=uuid4(),uuid4(),uuid4()
    first=receipt(lot,1,2,100)
    second=receipt(lot,3,2,200,value_seq=8)
    issue=move(lot,5,3,a,"ISSUE")
    assert replay([first,second,issue])==("LATE_VALUATION",{})
    second=receipt(lot,3,2,200)
    returned=move(lot,6,1,b,"RETURN")
    assert replay([first,second,issue,returned])==("UNPROVEN",{})
    with pytest.raises(Conflict):
        replay([first,second,issue],limit=1)
    invalid=list(first)
    invalid[15]=None
    assert replay([tuple(invalid),second,issue])==("UNPROVEN",{})
