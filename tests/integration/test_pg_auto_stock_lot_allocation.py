"""PG17 synthetic lot quantities, tenant isolation, audit and concurrency."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_allocation import mutate_stock_lot_allocation
from roboport_api.auto_stock_service import AutoStockDenied, AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_service import grant


def allocated(db,lot):
    return db.execute(
        "SELECT allocated FROM auto_stock_lot_balance WHERE lot_id=%s",(lot,)
    ).fetchone()[0]


def act(db,d,org,loc,lot,action,qty,key=None,actor=None):
    return mutate_stock_lot_allocation(
        db,d["tokens"][actor or d["intake"]],org,loc,lot,key or uuid4(),action,qty
    )


def setup(db,d,qty=5):
    org,loc,_=_org_and_bay(db)
    item=sku(db,org,loc)
    lot=add_lot(db,org,loc,item,add_bin(db,org,loc))
    grant_id=grant(db,d["intake"],org,loc)
    record(db,item,"RECEIPT",qty)
    return org,loc,item,lot,grant_id


def test_allocate_deallocate_replay_and_unallocated_issue_invariant():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d)
        key=uuid4()
        event=act(db,d,org,loc,lot,"ALLOCATE",4,key)
        assert act(db,d,org,loc,lot,"ALLOCATE",4,key)==event
        assert allocated(db,lot)==4
        assert balance(db,item)==(5,0)
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,lot,"ALLOCATE",2)
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,lot,"DEALLOCATE",5)
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,lot,"ALLOCATE",3,key)
        # Aggregate ISSUE may consume only the unallocated on-hand unit.
        record(db,item,"RESERVE",2)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("SELECT record_auto_work_order_stock(%s,%s,%s,%s,%s)",
                    (uuid4(),item,uuid4(),"ISSUE",2))
        act(db,d,org,loc,lot,"DEALLOCATE",2)
        assert allocated(db,lot)==2
        assert balance(db,item)==(5,2)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_allocation WHERE id=%s",(event,))


def test_foreign_lot_revocation_disabled_scope_and_invalid_inputs():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,grant_id=setup(db,d)
        other,other_loc,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            act(db,d,other,other_loc,lot,"ALLOCATE",1)
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,lot,"ALLOCATE",1,actor=d["manager"])
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,lot,"ALLOCATE",True)
        act(db,d,org,loc,lot,"ALLOCATE",2)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,lot,"DEALLOCATE",1)
        assert allocated(db,lot)==2


def test_direct_insert_cannot_overallocate_or_underflow():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        for action,qty in (("ALLOCATE",2),("DEALLOCATE",1)):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_stock_lot_allocation
                      (id,request_id,lot_id,sku_id,action,quantity)
                      VALUES(%s,%s,%s,%s,%s,%s)""",
                      (uuid4(),uuid4(),lot,item,action,qty))
        assert allocated(db,lot)==0


def test_concurrent_allocations_serialize_and_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            act(first,d,org,loc,lot,"ALLOCATE",1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    act(second,d,org,loc,lot,"ALLOCATE",1)
        with pytest.raises(AutoStockConflict):
            act(second,d,org,loc,lot,"ALLOCATE",1)
        assert allocated(second,lot)==1
        with pytest.raises(RuntimeError):
            with second.transaction():
                act(second,d,org,loc,lot,"DEALLOCATE",1)
                raise RuntimeError("rollback")
        assert allocated(second,lot)==1
