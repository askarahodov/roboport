"""PG17 reclassification: independent authorization, four-leg conservation and concurrency."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_reclassification import (
    propose_reclassification, approve_reclassification
)
from roboport_api.auto_stock_service import AutoStockDenied, AutoStockConflict
from roboport_api.auto_stock_serial import register_lot_serial
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,act,allocated
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_stock_bin_lot import add_lot
from test_pg_auto_stock_service import grant


def seeded(db,d,qty=4):
    org,loc,source_sku,source,warehouse_grant=setup(db,d,qty)
    destination_sku=sku(db,org,loc)
    bin_id=db.execute("SELECT bin_id FROM auto_stock_lot WHERE id=%s",(source,)).fetchone()[0]
    destination=add_lot(db,org,loc,destination_sku,bin_id,"RECLASS-TARGET")
    manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
    act(db,d,org,loc,source,"ALLOCATE",qty)
    return org,loc,source_sku,source,destination_sku,destination,warehouse_grant,manager_grant


def propose(db,d,org,loc,src,dst,qty=2,key=None,actor=None):
    return propose_reclassification(
        db,d["tokens"][actor or d["intake"]],org,loc,src,dst,key or uuid4(),
        qty,"Необходима корректировка артикула")


def approve(db,d,org,loc,doc,key=None,actor=None):
    return approve_reclassification(db,d["tokens"][actor or d["manager"]],
                                    org,loc,doc,key or uuid4())


def test_dual_control_balanced_reclassification_replay_and_audit():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,_,_=seeded(db,d)
        key=uuid4()
        doc=propose(db,d,org,loc,src,dst,2,key)
        assert propose(db,d,org,loc,src,dst,2,key)==doc
        assert balance(db,ss)==(4,0) and balance(db,ds)==(0,0)
        with pytest.raises(AutoStockConflict):
            propose(db,d,org,loc,src,dst,3,key)
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,loc,doc,actor=d["intake"])
        approval_key=uuid4()
        approved=approve(db,d,org,loc,doc,approval_key)
        assert approve(db,d,org,loc,doc,approval_key)==approved
        assert balance(db,ss)==(2,0) and balance(db,ds)==(2,0)
        assert allocated(db,src)==2 and allocated(db,dst)==2
        assert db.execute("""SELECT d.created_by,a.accepted_by,de.action,ce.action,
          da.action,ca.action,de.quantity,ce.quantity
          FROM auto_stock_reclassification_acceptance a
          JOIN auto_stock_reclassification_document d ON d.id=a.document_id
          JOIN auto_stock_event de ON de.id=a.debit_stock_id
          JOIN auto_stock_event ce ON ce.id=a.credit_stock_id
          JOIN auto_stock_lot_allocation da ON da.id=a.debit_allocation_id
          JOIN auto_stock_lot_allocation ca ON ca.id=a.credit_allocation_id
          WHERE a.id=%s""",(approved,)).fetchone()==(
            d["intake"],d["manager"],"RECLASS_OUT","RECLASS_IN",
            "DEALLOCATE","ALLOCATE",2,2)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc)
        assert db.execute("""SELECT discrepancy FROM auto_stock_reconciliation
          WHERE sku_id=%s""",(ss,)).fetchone()==(False,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_reclassification_document WHERE id=%s",(doc,))


def test_reservations_serials_and_revoke_block_independent_acceptance():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,wh,mg=seeded(db,d,3)
        doc=propose(db,d,org,loc,src,dst)
        record(db,ss,"RESERVE",2)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc)
        record(db,ss,"RELEASE",2)
        register_lot_serial(db,d["tokens"][d["intake"]],
                            org,loc,src,uuid4(),"SYN-SERIAL-001")
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(mg,))
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,loc,doc)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(wh,))
        with pytest.raises(AutoStockDenied):
            propose(db,d,org,loc,src,dst)


def test_tenant_self_approval_bad_scope_and_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,_,_=seeded(db,d,1)
        grant(db,d["intake"],org,loc,"MANAGER")
        with pytest.raises(AutoStockDenied):
            propose(db,d,uuid4(),loc,src,dst,1)
        with pytest.raises(AutoStockDenied):
            propose(db,d,org,uuid4(),src,dst,1)
        with pytest.raises(AutoStockConflict):
            propose(db,d,org,loc,src,src,1)
        doc=propose(db,d,org,loc,src,dst,1)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc,actor=d["intake"])
        with pytest.raises(RuntimeError):
            with db.transaction():
                approve(db,d,org,loc,doc)
                raise RuntimeError("rollback")
        assert balance(db,ss)==(1,0) and balance(db,ds)==(0,0)
        assert allocated(db,src)==1 and allocated(db,dst)==0


def test_concurrent_reclassification_serializes_on_both_skus():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,ss,src,ds,dst,_,_=seeded(db,d,1)
        doc=propose(db,d,org,loc,src,dst,1)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            approve(first,d,org,loc,doc)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    approve(second,d,org,loc,doc)
        with pytest.raises(AutoStockConflict):
            approve(second,d,org,loc,doc)
        assert balance(second,ss)==(0,0) and balance(second,ds)==(1,0)
