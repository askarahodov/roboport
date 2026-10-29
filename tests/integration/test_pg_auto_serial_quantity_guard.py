"""PG17: serial provenance survives any quantity-changing stock operation."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api.auto_stock_disposition import dispose_stock_lot
from roboport_api.auto_stock_lot_transfer import transfer_stock_lot
from roboport_api.auto_stock_service import AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup, act, allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_bin_lot import add_lot


def test_serial_quantity_guard_blocks_direct_sql_and_disposition():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,3)
        act(db,d,org,loc,lot,"ALLOCATE",3)
        for number in ("REGISTERED-001","REGISTERED-002"):
            register_lot_serial(db,d["tokens"][d["intake"]],
                                org,loc,lot,uuid4(),number)
        assert allocated(db,lot)==3
        # Deallocating one untracked piece is still allowed.
        act(db,d,org,loc,lot,"DEALLOCATE",1)
        assert allocated(db,lot)==2
        with pytest.raises(psycopg.errors.RaiseException,
                           match="registered serial inventory"):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_lot_allocation
                   (id,request_id,lot_id,sku_id,action,quantity)
                   VALUES(%s,%s,%s,%s,'DEALLOCATE',1)""",
                   (uuid4(),uuid4(),lot,item))
        with pytest.raises(AutoStockConflict):
            dispose_stock_lot(db,d["tokens"][d["intake"]],
                org,loc,lot,uuid4(),"WRITE_OFF",1,"inventory damage")
        assert allocated(db,lot)==2 and balance(db,item)==(3,0)


def test_unregistered_units_can_move_but_registered_serial_units_cannot():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,3)
        act(db,d,org,loc,lot,"ALLOCATE",3)
        bin_id=db.execute("SELECT bin_id FROM auto_stock_lot WHERE id=%s",
                          (lot,)).fetchone()[0]
        second=add_lot(db,org,loc,item,bin_id,"OTHER-BATCH")
        register_lot_serial(db,d["tokens"][d["intake"]],
                            org,loc,lot,uuid4(),"REGISTERED-TRANSFER-1")
        first=transfer_stock_lot(db,d["tokens"][d["intake"]],
                                 org,loc,lot,second,uuid4(),2)
        assert first is not None
        assert allocated(db,lot)==1 and allocated(db,second)==2
        with pytest.raises(AutoStockConflict):
            transfer_stock_lot(db,d["tokens"][d["intake"]],
                               org,loc,lot,second,uuid4(),1)
        assert allocated(db,lot)==1 and allocated(db,second)==2
