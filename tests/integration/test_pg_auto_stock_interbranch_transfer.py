"""PG17 interbranch transfer: two physical SKU ledgers, lot allocation, role isolation."""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_stock_interbranch_transfer import transfer_interbranch_stock
from roboport_api.auto_stock_service import AutoStockDenied, AutoStockConflict
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_bin,add_lot
from test_pg_auto_stock_lot_allocation import allocated,act
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def setup(db,d,quantity=5):
    org,src_loc,_=_org_and_bay(db)
    dst_loc=uuid4()
    db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
               (dst_loc,"DEST_"+dst_loc.hex[:8].upper(),org))
    src_sku=sku(db,org,src_loc)
    article=db.execute("SELECT article FROM auto_stock_sku WHERE id=%s",(src_sku,)).fetchone()[0]
    dst_sku=uuid4()
    db.execute("INSERT INTO auto_stock_sku(id,organization_id,location_id,article) VALUES(%s,%s,%s,%s)",
               (dst_sku,org,dst_loc,article))
    src_lot=add_lot(db,org,src_loc,src_sku,add_bin(db,org,src_loc))
    dst_lot=add_lot(db,org,dst_loc,dst_sku,add_bin(db,org,dst_loc))
    src_grant=grant(db,d["intake"],org,src_loc)
    dst_grant=grant(db,d["intake"],org,dst_loc)
    record(db,src_sku,"RECEIPT",quantity)
    act(db,d,org,src_loc,src_lot,"ALLOCATE",quantity)
    return org,src_loc,dst_loc,src_sku,dst_sku,src_lot,dst_lot,src_grant,dst_grant


def move(db,d,ctx,quantity,key=None):
    org,src_loc,dst_loc,_,_,src_lot,dst_lot,_,_=ctx
    return transfer_interbranch_stock(db,d["tokens"][d["intake"]],org,
        src_loc,dst_loc,src_lot,dst_lot,key or uuid4(),quantity)


def test_dual_ledgers_and_request_replay_and_immutable_journal():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=setup(db,d)
        org,a,b,sa,sb,la,lb,_,_=ctx
        request=uuid4()
        ident=move(db,d,ctx,3,request)
        assert move(db,d,ctx,3,request)==ident
        assert balance(db,sa)==(2,0) and balance(db,sb)==(3,0)
        assert allocated(db,la)==2 and allocated(db,lb)==3
        assert db.execute("""SELECT de.action,ce.action,da.action,ca.action
            FROM auto_stock_interbranch_transfer t
            JOIN auto_stock_event de ON de.id=t.debit_stock_id
            JOIN auto_stock_event ce ON ce.id=t.credit_stock_id
            JOIN auto_stock_lot_allocation da ON da.id=t.debit_allocation_id
            JOIN auto_stock_lot_allocation ca ON ca.id=t.credit_allocation_id
            WHERE t.id=%s""",(ident,)).fetchone()==(
                "TRANSFER_OUT","TRANSFER_IN","DEALLOCATE","ALLOCATE")
        with pytest.raises(AutoStockConflict):
            move(db,d,ctx,2,request)
        with pytest.raises(AutoStockConflict):
            move(db,d,ctx,3)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_interbranch_transfer WHERE id=%s",(ident,))


def test_both_grants_required_even_for_replay_and_tenant_scope():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=setup(db,d,2)
        org,src,dst,sa,sb,la,lb,sg,dg=ctx
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(dg,))
        with pytest.raises(AutoStockDenied):
            move(db,d,ctx,1)
        assert balance(db,sa)==(2,0) and balance(db,sb)==(0,0)
        new_dg=grant(db,d["intake"],org,dst)
        key=uuid4()
        move(db,d,ctx,1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(sg,))
        with pytest.raises(AutoStockDenied):
            move(db,d,ctx,1,key)
        assert allocated(db,la)==1 and allocated(db,lb)==1


def test_reserved_units_cannot_move_and_full_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=setup(db,d,3)
        _,_,_,sa,sb,la,lb,_,_=ctx
        record(db,sa,"RESERVE",2)
        with pytest.raises(AutoStockConflict):
            move(db,d,ctx,2)
        assert balance(db,sa)==(3,2) and balance(db,sb)==(0,0)
        with pytest.raises(RuntimeError):
            with db.transaction():
                move(db,d,ctx,1)
                raise RuntimeError("rollback")
        assert allocated(db,la)==3 and allocated(db,lb)==0
        assert balance(db,sa)==(3,2) and balance(db,sb)==(0,0)


def test_concurrent_transfer_single_source_serialization():
    d=_scenario()
    with psycopg.connect(DSN) as setup_db:
        ctx=setup(setup_db,d,1)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            move(first,d,ctx,1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    move(second,d,ctx,1)
        with pytest.raises(AutoStockConflict):
            move(second,d,ctx,1)
        _,_,_,sa,sb,la,lb,_,_=ctx
        assert balance(second,sa)==(0,0) and balance(second,sb)==(1,0)
        assert allocated(second,la)==0 and allocated(second,lb)==1
