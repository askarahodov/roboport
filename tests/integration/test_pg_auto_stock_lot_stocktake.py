"""PG17 audited inventory: count, stale approval and physical reconciliation."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_stocktake import count_stock_lot,approve_stock_lot_count
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated,act
from test_pg_auto_stock_ledger import balance,record
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant


def count(db,d,org,loc,lot,actual,key=None):
    return count_stock_lot(db,d["tokens"][d["intake"]],org,loc,lot,
                            key or uuid4(),actual,"physical recount")


def approve(db,d,org,loc,count_id,key=None):
    return approve_stock_lot_count(db,d["tokens"][d["manager"]],
                                   org,loc,count_id,key or uuid4())


def test_stocktake_gain_and_loss_preserve_lot_sku_parity():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,5)
        grant(db,d["manager"],org,loc,"MANAGER")
        act(db,d,org,loc,lot,"ALLOCATE",3)
        gain=count(db,d,org,loc,lot,5)
        assert db.execute("SELECT expected,actual FROM auto_stock_lot_count WHERE id=%s",(gain,)).fetchone()==(3,5)
        key=uuid4()
        approved=approve(db,d,org,loc,gain,key)
        assert approve(db,d,org,loc,gain,key)==approved
        assert balance(db,item)==(7,0) and allocated(db,lot)==5
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,gain)
        loss=count(db,d,org,loc,lot,2)
        approve(db,d,org,loc,loss)
        assert balance(db,item)==(4,0) and allocated(db,lot)==2
        unchanged=count(db,d,org,loc,lot,2)
        approve(db,d,org,loc,unchanged)
        assert balance(db,item)==(4,0) and allocated(db,lot)==2
        assert db.execute("SELECT count(*) FROM auto_stock_lot_count_approval WHERE count_id=%s",(unchanged,)).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_count WHERE id=%s",(gain,))


def test_stale_count_rollbacks_and_invalid_scope():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,role=setup(db,d,5)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
        count_id=count(db,d,org,loc,lot,3)
        act(db,d,org,loc,lot,"ALLOCATE",1)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,count_id)
        assert balance(db,item)==(5,0) and allocated(db,lot)==1
        other,foreign,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            approve(db,d,other,foreign,count_id)
        with pytest.raises(AutoStockDenied):
            count_stock_lot(db,d["tokens"][d["other"]],org,loc,lot,uuid4(),1,"test")
        new_count=count(db,d,org,loc,lot,2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                approve(db,d,org,loc,new_count)
                raise RuntimeError("rollback")
        assert balance(db,item)==(5,0) and allocated(db,lot)==1
        approve(db,d,org,loc,new_count)
        assert balance(db,item)==(6,0) and allocated(db,lot)==2
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(manager_grant,))
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,loc,new_count)


def test_count_loss_blocked_if_would_violate_reserved_physical_stock():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,5)
        grant(db,d["manager"],org,loc,"MANAGER")
        act(db,d,org,loc,lot,"ALLOCATE",4)
        record(db,item,"RESERVE",5)
        count_id=count(db,d,org,loc,lot,3)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,count_id)
        assert allocated(db,lot)==4 and balance(db,item)==(5,5)


def test_concurrent_approval_serializes_on_sku():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,3)
        grant(db,d["manager"],org,loc,"MANAGER")
        count_id=count(db,d,org,loc,lot,2)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            approve(first,d,org,loc,count_id)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    approve(second,d,org,loc,count_id)
        with pytest.raises(AutoStockConflict):
            approve(second,d,org,loc,count_id)
        assert allocated(second,lot)==2 and balance(second,item)==(5,0)
