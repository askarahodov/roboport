"""Bounded serial source replay: no averaging, no guessed transfer price."""
from uuid import uuid4

import pytest

from roboport_api.auto_serial_cost_replay import replay
from roboport_api.auto_closed_order_report import Conflict


def sample():
    lot,order=uuid4(),uuid4()
    receipt_a,receipt_b=uuid4(),uuid4()
    acceptance_a,acceptance_b=uuid4(),uuid4()
    val_a,val_b=uuid4(),uuid4()
    serial_a,serial_b=uuid4(),uuid4()
    issue_a,issue_b,return_b=uuid4(),uuid4(),uuid4()
    approval_a,approval_b=uuid4(),uuid4()
    history=[
        (lot,uuid4(),"ALLOCATE",1,receipt_a,acceptance_a,val_a,"RUB",
         1250,2,1,None,None,1,True,1,None),
        (lot,uuid4(),"ALLOCATE",1,receipt_b,acceptance_b,val_b,"RUB",
         2000,6,5,None,None,1,True,5,None),
        (lot,uuid4(),"DEALLOCATE",1,None,None,None,None,
         None,None,None,issue_a,None,1,True,9,order),
        (lot,uuid4(),"DEALLOCATE",1,None,None,None,None,
         None,None,None,issue_b,None,1,True,10,order),
        (lot,uuid4(),"ALLOCATE",1,None,None,None,None,
         None,None,None,None,return_b,1,True,11,order),
    ]
    moves=[
        (lot,None,serial_a,"ISSUE",issue_a,None,order,1,receipt_a,
         approval_a,3,4,lot,acceptance_a,val_a,2,"RUB",1250),
        (lot,None,serial_b,"ISSUE",issue_b,None,order,2,receipt_b,
         approval_b,7,8,lot,acceptance_b,val_b,6,"RUB",2000),
        (lot,None,serial_b,"RETURN",None,return_b,order,3,receipt_b,
         approval_b,7,8,lot,acceptance_b,val_b,6,"RUB",2000),
    ]
    return history,moves


def test_receipt_price_follows_specific_serial_on_return():
    history,moves=sample()
    status,parts=replay(history,moves)
    assert status=="SERIAL_TRACED"
    assert [x["unitNetMinor"] for m in moves for x in parts[m[4] or m[5]]]==[
        1250,2000,2000]
    assert parts[moves[2][5]][0]["originalIssueId"]==str(moves[1][4])
    assert parts[moves[2][5]][0]["serialApprovalId"]==str(moves[1][9])


def test_serial_origin_refuses_unpaired_transfer_approval_and_wrong_order():
    history,moves=sample()
    without_approval=list(moves[1]);without_approval[9]=None
    assert replay(history,[moves[0],tuple(without_approval),moves[2]])==("UNPROVEN",{})
    moved=list(moves[0]);moved[3]="TRANSFER";moved[1]=uuid4()
    assert replay(history,[tuple(moved),*moves[1:]])==("UNPROVEN",{})
    cross_order=list(moves[2]);cross_order[6]=uuid4()
    assert replay(history,[*moves[:2],tuple(cross_order)])==("UNPROVEN",{})
    assert replay(history,moves[:2])==("UNPROVEN",{})
    with pytest.raises(Conflict):
        replay(history,moves,limit=2)


def test_serial_late_purchase_cost_never_becomes_zero():
    history,moves=sample()
    late=list(moves[1]);late[15]=11
    assert replay(history,[moves[0],tuple(late),moves[2]])==("UNPROVEN",{})
    history_late=list(history)
    b=list(history_late[1]);b[9]=10;history_late[1]=tuple(b)
    moves_late=[moves[0],list(moves[1]),list(moves[2])]
    moves_late[1][15]=10;moves_late[2][15]=10
    assert replay(history_late,[moves_late[0],tuple(moves_late[1]),tuple(moves_late[2])])==(
        "LATE_VALUATION",{})
