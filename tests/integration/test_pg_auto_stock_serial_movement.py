"""PG17 tracked serial units: WorkOrder, return and interbranch conservation."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api.auto_stock_serial_movement import move_lot_serial
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from roboport_api.auto_stock_interbranch_transfer import transfer_interbranch_stock
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup as work_order_setup
from test_pg_auto_stock_interbranch_transfer import setup as transfer_setup
from test_pg_auto_stock_bin_lot import add_lot
from test_pg_auto_stock_ledger import balance,record
from test_pg_auto_stock_lot_allocation import allocated,act as lot_action


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
        db.commit()
        with pytest.raises(AutoStockConflict):
            issue_work_order_from_lot(db,d["tokens"][d["intake"]],
                                      org,loc,order,lot,uuid4(),1)
        # The owner-only SQL function remains protected independently of
        # the Python service and must fail at deferred-constraint checking.
        with pytest.raises(psycopg.errors.RaiseException,match="strands registered"):
            with db.transaction():
                db.execute("SELECT record_auto_stock_lot_work_order_issue(%s,%s,%s,1)",
                           (order,lot,uuid4()))
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



def test_quantitative_return_cannot_strand_an_issued_serial():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=work_order_setup(db,d,1)
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,loc,lot,uuid4(),"STRAND-RETURN")
        move(db,d,org,serial,"ISSUE",order=order)
        db.commit()  # Isolate the attempted ordinary return from prior deferred events.
        # The Python service must return a conflict, not leak a deferred SQL
        # error at HTTP connection close.
        with pytest.raises(AutoStockConflict):
            return_work_order_to_lot(db,d["tokens"][d["intake"]],
                                     org,loc,order,lot,uuid4(),1)
        # Direct SQL cannot bypass the same constraint.
        with pytest.raises(psycopg.errors.RaiseException,match="strands issued serial"):
            with db.transaction():
                db.execute("SELECT record_auto_stock_lot_work_order_return(%s,%s,%s,1)",
                           (order,lot,uuid4()))
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        assert balance(db,sku)==(0,0) and allocated(db,lot)==0
        assert db.execute("SELECT state FROM auto_stock_serial_current WHERE serial_id=%s",
                          (serial,)).fetchone()==("ISSUED",)
        move(db,d,org,serial,"RETURN",order=order)
        assert balance(db,sku)==(1,0) and allocated(db,lot)==1


def test_transfer_rejects_duplicate_serial_in_another_lot_of_target_sku():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=transfer_setup(db,d,2)
        org,a,b,sa,sb,la,lb,_,_=ctx
        origin=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,a,la,uuid4(),"DUPLICATE-TRACK")
        bin_id=db.execute("SELECT bin_id FROM auto_stock_lot WHERE id=%s",(lb,)).fetchone()[0]
        separate_lot=add_lot(db,org,b,sb,bin_id,"SECOND-BATCH")
        record(db,sb,"RECEIPT",1)
        lot_action(db,d,org,b,separate_lot,"ALLOCATE",1)
        register_lot_serial(db,d["tokens"][d["intake"]],
                            org,b,separate_lot,uuid4(),"DUPLICATE-TRACK")
        with pytest.raises(AutoStockConflict):
            move(db,d,org,origin,"TRANSFER",dest=lb)
        assert allocated(db,la)==2 and allocated(db,lb)==0
        assert balance(db,sa)==(2,0) and balance(db,sb)==(1,0)


def test_registration_after_transfer_checks_dynamic_lot_capacity_and_sku():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=transfer_setup(db,d,2)
        org,a,b,sa,sb,la,lb,_,_=ctx
        origin=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,a,la,uuid4(),"DYNAMIC-TRACK")
        move(db,d,org,origin,"TRANSFER",dest=lb)
        with pytest.raises(AutoStockConflict):
            register_lot_serial(db,d["tokens"][d["intake"]],
                                org,b,lb,uuid4(),"DIFFERENT-NUMBER")
        record(db,sb,"RECEIPT",1)
        lot_action(db,d,org,b,lb,"ALLOCATE",1)
        with pytest.raises(AutoStockConflict):
            register_lot_serial(db,d["tokens"][d["intake"]],
                                org,b,lb,uuid4(),"DYNAMIC-TRACK")
        new=register_lot_serial(db,d["tokens"][d["intake"]],
                               org,b,lb,uuid4(),"DIFFERENT-NUMBER")
        assert new!=origin
        assert allocated(db,lb)==2



def test_direct_late_serial_transfer_cannot_use_historical_physical_ledger():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        ctx=transfer_setup(db,d,2)
        org,src_loc,dst_loc,src_sku,dst_sku,src_lot,dst_lot,_,_=ctx
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
                                  org,src_loc,src_lot,uuid4(),"LATE-MOVE-001")
        physical=transfer_interbranch_stock(
            db,d["tokens"][d["intake"]],org,src_loc,dst_loc,
            src_lot,dst_lot,uuid4(),1)
        transfer_interbranch_stock(db,d["tokens"][d["intake"]],org,
                 dst_loc,src_loc,dst_lot,src_lot,uuid4(),1)
        assert allocated(db,dst_lot)==0 and allocated(db,src_lot)==2
        db.commit()  # Old physical transfer checks have already committed.
        with pytest.raises(psycopg.errors.RaiseException,match="exceeds physical lot capacity"):
            with db.transaction():
                db.execute("SELECT set_config('roboport.serial_actor',%s,true)",
                           (str(d["intake"]),))
                db.execute("""INSERT INTO auto_stock_serial_movement(
                    id,request_id,serial_id,action,source_lot_id,
                    destination_lot_id,interbranch_id,performed_by)
                    VALUES(%s,%s,%s,'TRANSFER',%s,%s,%s,%s)""",
                    (uuid4(),uuid4(),serial,src_lot,dst_lot,physical,d["intake"]))
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        assert db.execute("SELECT lot_id FROM auto_stock_serial_current WHERE serial_id=%s",
                          (serial,)).fetchone()[0]==src_lot
        assert balance(db,src_sku)==(2,0) and balance(db,dst_sku)==(0,0)
