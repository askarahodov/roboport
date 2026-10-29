"""PG17 issue from a specific lot into an existing work order."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_work_order_stock import work_order,act as order_action
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import allocated,act as lot_action
from test_pg_auto_stock_service import grant


def setup(db,d,qty=5):
    org,loc,bay=_org_and_bay(db)
    order,_=work_order(db,d,org,loc,bay)
    item=sku(db,org,loc)
    lot=add_lot(db,org,loc,item,add_bin(db,org,loc))
    role=grant(db,d["intake"],org,loc)
    record(db,item,"RECEIPT",qty)
    lot_action(db,d,org,loc,lot,"ALLOCATE",qty)
    order_action(db,d,org,loc,order,item,"RESERVE",qty)
    return org,loc,order,item,lot,role


def issue(db,d,org,loc,order,lot,qty,key=None,actor=None):
    return issue_work_order_from_lot(
        db,d["tokens"][actor or d["intake"]],org,loc,
        order,lot,key or uuid4(),qty)


def test_lot_issue_atomic_three_ledgers_and_replay():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d)
        key=uuid4()
        ident=issue(db,d,org,loc,order,lot,3,key)
        assert issue(db,d,org,loc,order,lot,3,key)==ident
        assert allocated(db,lot)==2
        assert balance(db,item)==(2,2)
        row=db.execute("""SELECT d.action,e.action,w.action,i.quantity
          FROM auto_stock_lot_work_order_issue i
          JOIN auto_stock_lot_allocation d ON d.id=i.lot_debit_id
          JOIN auto_stock_event e ON e.id=i.stock_event_id
          JOIN auto_work_order_stock_event w ON w.stock_event_id=e.id
          WHERE i.id=%s""",(ident,)).fetchone()
        assert row==("DEALLOCATE","ISSUE","ISSUE",3)
        with pytest.raises(AutoStockConflict):
            issue(db,d,org,loc,order,lot,2,key)
        with pytest.raises(AutoStockConflict):
            issue(db,d,org,loc,order,lot,3)
        assert allocated(db,lot)==2 and balance(db,item)==(2,2)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_work_order_issue WHERE id=%s",(ident,))


def test_failed_issue_does_not_consume_lot_or_order():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,2)
        with pytest.raises(AutoStockConflict):
            issue(db,d,org,loc,order,lot,3)
        assert allocated(db,lot)==2 and balance(db,item)==(2,2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                issue(db,d,org,loc,order,lot,1)
                raise RuntimeError("rollback")
        assert allocated(db,lot)==2 and balance(db,item)==(2,2)
        assert db.execute(
            "SELECT count(*) FROM auto_stock_lot_work_order_issue WHERE work_order_id=%s",
            (order,)).fetchone()[0]==0


def test_wrong_scope_role_and_revoke():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,role=setup(db,d,2)
        foreign,other,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            issue(db,d,foreign,other,order,lot,1)
        with pytest.raises(AutoStockDenied):
            issue(db,d,org,loc,order,lot,1,actor=d["manager"])
        key=uuid4()
        issue(db,d,org,loc,order,lot,1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
        with pytest.raises(AutoStockDenied):
            issue(db,d,org,loc,order,lot,1,key)


def test_concurrent_issue_serializes_and_never_double_consumes():
    d=_scenario()
    with psycopg.connect(DSN) as setup_db:
        org,loc,order,item,lot,_=setup(setup_db,d,1)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            issue(first,d,org,loc,order,lot,1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    issue(second,d,org,loc,order,lot,1)
        with pytest.raises(AutoStockConflict):
            issue(second,d,org,loc,order,lot,1)
        assert allocated(second,lot)==0 and balance(second,item)==(0,0)
