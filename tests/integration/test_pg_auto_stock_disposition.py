"""PG17 auditable supplier return / write-off of warehouse parts."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_disposition import dispose_stock_lot
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated,act
from test_pg_auto_stock_ledger import record,balance
from test_pg_auto_bay_booking import _org_and_bay


def dispose(db,d,org,loc,lot,action,qty,key=None,actor=None,ref=None):
    return dispose_stock_lot(db,d["tokens"][actor or d["intake"]],
                            org,loc,lot,key or uuid4(),action,qty,
                            "physical disposition",ref)


def test_writeoff_and_supplier_return_consume_physical_and_lot_balances():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,6)
        act(db,d,org,loc,lot,"ALLOCATE",6)
        key=uuid4()
        ident=dispose(db,d,org,loc,lot,"WRITE_OFF",2,key)
        assert dispose(db,d,org,loc,lot,"WRITE_OFF",2,key)==ident
        assert allocated(db,lot)==4 and balance(db,item)==(4,0)
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"WRITE_OFF",3,key)
        returned=dispose(db,d,org,loc,lot,"SUPPLIER_RETURN",3,ref="SUP-CASE-1")
        assert allocated(db,lot)==1 and balance(db,item)==(1,0)
        row=db.execute("""SELECT de.action,a.action,d.action,d.supplier_reference
          FROM auto_stock_lot_disposition d
          JOIN auto_stock_event de ON de.id=d.stock_event_id
          JOIN auto_stock_lot_allocation a ON a.id=d.lot_debit_id
          WHERE d.id=%s""",(returned,)).fetchone()
        assert row==("SUPPLIER_RETURN","DEALLOCATE","SUPPLIER_RETURN","SUP-CASE-1")
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"SUPPLIER_RETURN",2,ref="SUP-CASE-1")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_disposition WHERE id=%s",(ident,))


def test_reserved_units_cannot_be_written_off_and_transaction_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,4)
        act(db,d,org,loc,lot,"ALLOCATE",4)
        record(db,item,"RESERVE",3)
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"WRITE_OFF",2)
        assert balance(db,item)==(4,3) and allocated(db,lot)==4
        with pytest.raises(RuntimeError):
            with db.transaction():
                dispose(db,d,org,loc,lot,"WRITE_OFF",1)
                raise RuntimeError("rollback")
        assert balance(db,item)==(4,3) and allocated(db,lot)==4


def test_wrong_tenant_role_and_revocation():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,role=setup(db,d,2)
        act(db,d,org,loc,lot,"ALLOCATE",2)
        other,foreign,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            dispose(db,d,other,foreign,lot,"WRITE_OFF",1)
        with pytest.raises(AutoStockDenied):
            dispose(db,d,org,loc,lot,"WRITE_OFF",1,actor=d["manager"])
        key=uuid4()
        dispose(db,d,org,loc,lot,"WRITE_OFF",1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
        with pytest.raises(AutoStockDenied):
            dispose(db,d,org,loc,lot,"WRITE_OFF",1,key)


def test_concurrent_disposition_serializes_on_sku():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        act(db,d,org,loc,lot,"ALLOCATE",1)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            dispose(first,d,org,loc,lot,"WRITE_OFF",1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    dispose(second,d,org,loc,lot,"WRITE_OFF",1)
        with pytest.raises(AutoStockConflict):
            dispose(second,d,org,loc,lot,"WRITE_OFF",1)
        assert balance(second,item)==(0,0) and allocated(second,lot)==0
