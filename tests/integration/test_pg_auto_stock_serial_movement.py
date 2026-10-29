"""PG17 tracked serial units: WorkOrder, return and interbranch conservation."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api.auto_stock_serial_movement import move_lot_serial
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup as work_order_setup
from test_pg_auto_stock_interbranch_transfer import setup as transfer_setup
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_lot_allocation import allocated


def move(db,d,org,serial,action,order=None,dest=None,key=None,actor=None,scope=None):
    location=scope or db.execute("SELECT location_id FROM auto_stock_serial_current WHERE serial_id=%s",(serial,)).fetchone()[0]
    return move_lot_serial(db,d["tokens"][actor or d["intake"]],
           org,location,serial,key or uuid4(),action,order,dest)


def test_serial_issue_return_reissue_and_idempotency_preserve_ledger():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,grant=work_order_setup(db,d,3)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,loc,lot,uuid4(),"TRACKED-001")
        first_key=uuid4()
        issued=move(db,d,org,serial,"ISSUE",order=order,key=first_key)
        assert move(db,d,org,serial,"ISSUE",order=order,key=first_key)==issued
        assert balance(db,sku)==(2,2) and allocated(db,lot)==2
        assert db.execute("""SELECT state,lot_id,work_order_id FROM auto_stock_serial_current
          WHERE serial_id=%s""",(serial,)).fetchone()==("ISSUED",lot,order)
        with pytest.raises(AutoStockConflict):
            move(db,d,org,serial,"ISSUE",order=order)
        with pytest.raises(AutoStockConflict):
            move(db,d,org,serial,"RETURN",order=uuid4())
        ret=move(db,d,org,serial,"RETURN",order=order)
        assert db.execute("""SELECT state,lot_id,work_order_id FROM auto_stock_serial_current
          WHERE serial_id=%s""",(serial,)).fetchone()==("IN_STOCK",lot,None)
        assert balance(db,sku)==(3,2) and allocated(db,lot)==3
        assert db.execute("""SELECT COUNT(*) FROM auto_stock_serial_movement
             WHERE serial_id=%s""",(serial,)).fetchone()[0]==2
        move(db,d,org,serial,"ISSUE",order=order)
        assert balance(db,sku)==(2,1)
        assert move(db,d,org,serial,"ISSUE",order=order,key=first_key)==issued
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_serial_movement WHERE id=%s",(issued,))
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant,))
        with pytest.raises(AutoStockDenied):
            move(db,d,org,serial,"RETURN",order=order)


def test_serial_interbranch_moves_identity_and_returns_to_origin():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=transfer_setup(db,d,2)
        org,loc1,loc2,sku1,sku2,lot1,lot2,_,dest_grant=ctx
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                           org,loc1,lot1,uuid4(),"TRACKED-MOVED")
        key=uuid4()
        transferred=move(db,d,org,serial,"TRANSFER",dest=lot2,key=key)
        assert move(db,d,org,serial,"TRANSFER",dest=lot2,key=key,scope=loc1)==transferred
        assert balance(db,sku1)==(1,0) and balance(db,sku2)==(1,0)
        assert allocated(db,lot1)==1 and allocated(db,lot2)==1
        assert db.execute("""SELECT state,lot_id,location_id,sku_id
            FROM auto_stock_serial_current WHERE serial_id=%s""",(serial,)).fetchone()==(
                "IN_STOCK",lot2,loc2,sku2)
        with pytest.raises(AutoStockDenied):
            move(db,d,org,serial,"TRANSFER",dest=lot2)
        move(db,d,org,serial,"TRANSFER",dest=lot1)
        assert db.execute("""SELECT lot_id,location_id FROM auto_stock_serial_current
          WHERE serial_id=%s""",(serial,)).fetchone()==(lot1,loc1)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (dest_grant,))
        with pytest.raises(AutoStockDenied):
            move(db,d,org,serial,"TRANSFER",dest=lot2)


def test_untracked_lot_deallocation_cannot_strand_registered_serial():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=work_order_setup(db,d,1)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,loc,lot,uuid4(),"TRACKED-STRAND")
        with pytest.raises(psycopg.errors.RaiseException,match="strands registered"):
            with db.transaction():
                issue_work_order_from_lot(db,d["tokens"][d["intake"]],
                                          org,loc,order,lot,uuid4(),1)
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        assert allocated(db,lot)==1 and balance(db,sku)==(1,1)
        assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",
                          (serial,)).fetchone()==("IN_STOCK",)
        # Direct SQL is subject to the same deferred invariant, not just service checks.
        with pytest.raises(psycopg.errors.RaiseException,match="strands registered"):
            with db.transaction():
                db.execute("""SELECT record_auto_stock_lot_allocation(
                  %s,%s,'DEALLOCATE',1)""",(lot,uuid4()))
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        assert allocated(db,lot)==1


def test_invalid_actor_and_wrong_tenant_serial_never_moved():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=work_order_setup(db,d,1)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,loc,lot,uuid4(),"TRACKED-TENANT")
        with pytest.raises(AutoStockDenied):
            move(db,d,uuid4(),serial,"ISSUE",order=order)
        with pytest.raises(AutoStockDenied):
            move(db,d,org,serial,"ISSUE",order=order,actor=d["manager"])
        with pytest.raises(AutoStockDenied):
            move(db,d,org,serial,"TRANSFER",dest=lot)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_serial_movement(
                 id,request_id,serial_id,action,source_lot_id,
                 work_order_id,issue_id,performed_by)
                 VALUES(%s,%s,%s,'ISSUE',%s,%s,%s,%s)""",
                 (uuid4(),uuid4(),serial,lot,order,uuid4(),d["intake"]))
