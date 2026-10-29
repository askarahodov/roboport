"""PG17 staged warehouse receiving: maker/checker, idempotent inventory and ACL."""
from uuid import uuid4

import psycopg
import pytest
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document,accept_receipt_document
)
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant


def test_receipt_only_posts_on_independent_acceptance():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,2)
        grant(db,d["manager"],org,loc,"MANAGER")
        request=uuid4()
        doc=create_receipt_document(db,d["tokens"][d["intake"]],
              org,loc,lot,request,3,"SUPPLIER-REF-123")
        assert create_receipt_document(db,d["tokens"][d["intake"]],
              org,loc,lot,request,3,"SUPPLIER-REF-123")==doc
        assert balance(db,item)==(2,0) and allocated(db,lot)==0
        with pytest.raises(AutoStockConflict):
            create_receipt_document(db,d["tokens"][d["intake"]],
                org,loc,lot,request,4,"SUPPLIER-REF-123")
        with pytest.raises(AutoStockDenied):
            accept_receipt_document(db,d["tokens"][d["intake"]],
                org,loc,doc,uuid4())
        approve_key=uuid4()
        acceptance=accept_receipt_document(db,d["tokens"][d["manager"]],
                org,loc,doc,approve_key)
        assert accept_receipt_document(db,d["tokens"][d["manager"]],
                org,loc,doc,approve_key)==acceptance
        assert balance(db,item)==(5,0) and allocated(db,lot)==3
        assert db.execute("""SELECT d.created_by,a.accepted_by,e.action,e.quantity,l.action,l.quantity
          FROM auto_stock_receipt_acceptance a
          JOIN auto_stock_receipt_document d ON d.id=a.document_id
          JOIN auto_stock_event e ON e.id=a.stock_event_id
          JOIN auto_stock_lot_allocation l ON l.id=a.allocation_event_id
          WHERE a.id=%s""",(acceptance,)).fetchone()==(
             d["intake"],d["manager"],"RECEIPT",3,"ALLOCATE",3)
        with pytest.raises(AutoStockConflict):
            accept_receipt_document(db,d["tokens"][d["manager"]],
                org,loc,doc,uuid4())
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_receipt_document WHERE id=%s",(doc,))


def test_dual_grant_cannot_self_accept_direct_insert_denied():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        grant(db,d["intake"],org,loc,"MANAGER")
        doc=create_receipt_document(db,d["tokens"][d["intake"]],
            org,loc,lot,uuid4(),1,"STOCK-REF")
        with pytest.raises(AutoStockConflict):
            accept_receipt_document(db,d["tokens"][d["intake"]],
                org,loc,doc,uuid4())
        assert balance(db,item)==(1,0)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_receipt_document
                  (id,request_id,lot_id,sku_id,organization_id,location_id,quantity,
                   source_reference,created_by)
                  VALUES(%s,%s,%s,%s,%s,%s,1,'DIRECT-REF',%s)""",
                  (uuid4(),uuid4(),lot,item,org,loc,d["manager"]))


def test_revocation_scope_and_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,counter_grant=setup(db,d,1)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
        doc=create_receipt_document(db,d["tokens"][d["intake"]],
            org,loc,lot,uuid4(),2,"SUPPLIER-ONE")
        with pytest.raises(AutoStockDenied):
            accept_receipt_document(db,d["tokens"][d["manager"]],
                org,uuid4(),doc,uuid4())
        with pytest.raises(RuntimeError):
            with db.transaction():
                accept_receipt_document(db,d["tokens"][d["manager"]],
                    org,loc,doc,uuid4())
                raise RuntimeError("rollback")
        assert balance(db,item)==(1,0) and allocated(db,lot)==0
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (manager_grant,))
        with pytest.raises(AutoStockDenied):
            accept_receipt_document(db,d["tokens"][d["manager"]],
                org,loc,doc,uuid4())
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (counter_grant,))
        with pytest.raises(AutoStockDenied):
            create_receipt_document(db,d["tokens"][d["intake"]],
                org,loc,lot,uuid4(),2,"SUPPLIER-ONE")


def test_acceptance_sku_lock_serializes_repeat_request():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        grant(db,d["manager"],org,loc,"MANAGER")
        doc=create_receipt_document(db,d["tokens"][d["intake"]],
            org,loc,lot,uuid4(),2,"SUPPLIER-TWO")
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            accept_receipt_document(first,d["tokens"][d["manager"]],
                org,loc,doc,uuid4())
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    accept_receipt_document(second,d["tokens"][d["manager"]],
                        org,loc,doc,uuid4())
        with pytest.raises(AutoStockConflict):
            accept_receipt_document(second,d["tokens"][d["manager"]],
                org,loc,doc,uuid4())
        assert balance(second,item)==(3,0) and allocated(second,lot)==2
