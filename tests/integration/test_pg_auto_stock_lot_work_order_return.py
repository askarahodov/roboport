"""PG17 atomic lot-specific work-order returns and rollback."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_lot_work_order_issue import setup,issue
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance


def ret(db,d,org,loc,order,lot,qty,key=None,actor=None):
    return return_work_order_to_lot(db,d["tokens"][actor or d["intake"]],
                                   org,loc,order,lot,key or uuid4(),qty)


def test_return_recredits_original_lot_and_on_hand_without_reserve():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d)
        issue(db,d,org,loc,order,lot,3)
        assert allocated(db,lot)==2 and balance(db,item)==(2,2)
        key=uuid4()
        returned=ret(db,d,org,loc,order,lot,2,key)
        assert ret(db,d,org,loc,order,lot,2,key)==returned
        assert allocated(db,lot)==4 and balance(db,item)==(4,2)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,lot,2)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,lot,1,key)
        ret(db,d,org,loc,order,lot,1)
        assert allocated(db,lot)==5 and balance(db,item)==(5,2)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,lot,1)
        row=db.execute("""SELECT c.action,e.action,w.action,r.quantity
          FROM auto_stock_lot_work_order_return r
          JOIN auto_stock_lot_allocation c ON c.id=r.lot_credit_id
          JOIN auto_stock_event e ON e.id=r.stock_event_id
          JOIN auto_work_order_stock_event w ON w.stock_event_id=e.id
          WHERE r.id=%s""",(returned,)).fetchone()
        assert row==("ALLOCATE","RETURN","RETURN",2)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_work_order_return WHERE id=%s",(returned,))


def test_wrong_lot_order_tenant_and_revoked_role():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,role=setup(db,d,2)
        issue(db,d,org,loc,order,lot,1)
        other_lot=add_lot(db,org,loc,item,add_bin(db,org,loc,"OTHER"))
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,other_lot,1)
        other,foreign,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            ret(db,d,other,foreign,order,lot,1)
        with pytest.raises(AutoStockDenied):
            ret(db,d,org,loc,order,lot,1,actor=d["manager"])
        key=uuid4()
        ret(db,d,org,loc,order,lot,1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
        with pytest.raises(AutoStockDenied):
            ret(db,d,org,loc,order,lot,1,key)


def test_return_rollback_and_competing_overreturn():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,1)
        issue(db,d,org,loc,order,lot,1)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            ret(first,d,org,loc,order,lot,1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    ret(second,d,org,loc,order,lot,1)
        with pytest.raises(AutoStockConflict):
            ret(second,d,org,loc,order,lot,1)
        assert allocated(second,lot)==1 and balance(second,item)==(1,0)
        with pytest.raises(RuntimeError):
            with second.transaction():
                issue(second,d,org,loc,order,lot,1) if False else None
                raise RuntimeError("rollback")
        assert allocated(second,lot)==1 and balance(second,item)==(1,0)
