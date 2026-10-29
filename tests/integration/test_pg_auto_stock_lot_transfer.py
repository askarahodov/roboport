"""PG17 atomic transfer across existing lots in one branch."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_lot_transfer import transfer_stock_lot
from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import allocated,act
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def setup(db,d,qty=5):
    org,loc,_=_org_and_bay(db)
    item=sku(db,org,loc)
    first=add_lot(db,org,loc,item,add_bin(db,org,loc,"BIN-A"))
    second=add_lot(db,org,loc,item,add_bin(db,org,loc,"BIN-B"))
    grant_id=grant(db,d["intake"],org,loc)
    record(db,item,"RECEIPT",qty)
    act(db,d,org,loc,first,"ALLOCATE",qty)
    return org,loc,item,first,second,grant_id


def move(db,d,org,loc,src,dst,qty,key=None,actor=None):
    return transfer_stock_lot(db,d["tokens"][actor or d["intake"]],
                              org,loc,src,dst,key or uuid4(),qty)


def test_transfer_pair_preserves_physical_stock_and_idempotency():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,src,dst,_=setup(db,d)
        key=uuid4()
        ident=move(db,d,org,loc,src,dst,3,key)
        assert move(db,d,org,loc,src,dst,3,key)==ident
        assert allocated(db,src)==2 and allocated(db,dst)==3
        assert balance(db,item)==(5,0)
        row=db.execute("""SELECT d.action,c.action,t.quantity
            FROM auto_stock_lot_transfer t
            JOIN auto_stock_lot_allocation d ON d.id=t.debit_event_id
            JOIN auto_stock_lot_allocation c ON c.id=t.credit_event_id
            WHERE t.id=%s""",(ident,)).fetchone()
        assert row==("DEALLOCATE","ALLOCATE",3)
        with pytest.raises(AutoStockConflict):
            move(db,d,org,loc,src,dst,2,key)
        with pytest.raises(AutoStockConflict):
            move(db,d,org,loc,src,dst,3)
        assert allocated(db,src)==2 and allocated(db,dst)==3
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_transfer WHERE id=%s",(ident,))


def test_cross_scope_role_revoke_and_branch():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,src,dst,grant_id=setup(db,d)
        other,foreignloc,_=_org_and_bay(db)
        other_item=sku(db,other,foreignloc)
        foreign=add_lot(db,other,foreignloc,other_item,add_bin(db,other,foreignloc,"BIN-F"))
        with pytest.raises(AutoStockDenied):
            move(db,d,org,loc,src,foreign,1)
        with pytest.raises(AutoStockDenied):
            move(db,d,other,foreignloc,src,dst,1)
        with pytest.raises(AutoStockDenied):
            move(db,d,org,loc,src,dst,1,actor=d["manager"])
        key=uuid4()
        move(db,d,org,loc,src,dst,1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            move(db,d,org,loc,src,dst,1,key)


def test_atomic_rollback_and_concurrent_transfer():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,src,dst,_=setup(db,d,1)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            move(first,d,org,loc,src,dst,1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    move(second,d,org,loc,src,dst,1)
        with pytest.raises(AutoStockConflict):
            move(second,d,org,loc,src,dst,1)
        assert allocated(second,src)==0 and allocated(second,dst)==1
        with pytest.raises(RuntimeError):
            with second.transaction():
                move(second,d,org,loc,dst,src,1)
                raise RuntimeError("rollback")
        assert allocated(second,src)==0 and allocated(second,dst)==1
