"""Supplier return: independent approval, physical balance and purchase-cost audit."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_supplier_return_document import (
    draft_supplier_return,approve_supplier_return)
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt,value
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance,record


def draft(db,d,org,loc,receipt,quantity=1,key=None,actor=None):
    return draft_supplier_return(db,d["tokens"][actor or d["intake"]],
        org,loc,receipt,key or uuid4(),quantity,
        "Согласованный возврат по браку","SUPPLIER-RMA-001")


def approve(db,d,org,loc,doc,key=None,actor=None):
    return approve_supplier_return(db,d["tokens"][actor or d["manager"]],
                                  org,loc,doc,key or uuid4())


def test_supplier_return_two_person_audit_replay_and_exact_vat_allocation():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,3)
        value(db,d,org,loc,receipt,vat=601)
        key=uuid4()
        first=draft(db,d,org,loc,receipt,1,key)
        assert draft(db,d,org,loc,receipt,1,key)==first
        assert balance(db,sku)==(4,0) and allocated(db,lot)==3
        with pytest.raises(AutoStockConflict):
            draft(db,d,org,loc,receipt,2,key)
        key2=uuid4()
        first_accept=approve(db,d,org,loc,first,key2)
        assert approve(db,d,org,loc,first,key2)==first_accept
        assert balance(db,sku)==(3,0) and allocated(db,lot)==2
        second=draft(db,d,org,loc,receipt,2)
        second_accept=approve(db,d,org,loc,second)
        assert balance(db,sku)==(1,0) and allocated(db,lot)==0
        amounts=db.execute("""SELECT d.quantity,a.purchase_net_attributed_minor,
          a.purchase_vat_attributed_minor,v.currency,p.action,p.quantity,
          p.supplier_reference,d.created_by,a.accepted_by
          FROM auto_stock_supplier_return_acceptance a
          JOIN auto_stock_supplier_return_document d ON d.id=a.document_id
          JOIN auto_stock_lot_disposition p ON p.id=a.disposition_id
          JOIN auto_stock_receipt_valuation v ON v.id=a.source_valuation_id
          WHERE a.id IN (%s,%s) ORDER BY d.quantity""",
          (first_accept,second_accept)).fetchall()
        assert amounts==[
          (1,1250,200,"RUB","SUPPLIER_RETURN",1,"SUPPLIER-RMA-001",
             d["intake"],d["manager"]),
          (2,2500,401,"RUB","SUPPLIER_RETURN",2,"SUPPLIER-RMA-001",
             d["intake"],d["manager"])]
        assert db.execute("""SELECT sum(purchase_net_attributed_minor),
          sum(purchase_vat_attributed_minor) FROM auto_stock_supplier_return_acceptance
          WHERE source_valuation_id=(SELECT id FROM auto_stock_receipt_valuation
                                     WHERE document_id=%s)""",(receipt,)).fetchone()==(3750,601)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,first)
        assert db.execute("SELECT count(*) FROM auto_stock_supplier_return_acceptance").fetchone()[0]>=2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_supplier_return_document WHERE id=%s",(first,))


def test_pending_document_does_not_move_stock_and_overdraw_denied():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        with pytest.raises(AutoStockConflict):
            draft(db,d,org,loc,receipt,2)
        value(db,d,org,loc,receipt,vat=101)
        doc=draft(db,d,org,loc,receipt,2)
        assert balance(db,sku)==(3,0) and allocated(db,lot)==2
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,uuid4(),doc)
        with pytest.raises(AutoStockDenied):
            draft(db,d,uuid4(),loc,receipt)
        record(db,sku,"RESERVE",2)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc)
        record(db,sku,"RELEASE",2)
        approved=approve(db,d,org,loc,doc)
        assert approved
        assert balance(db,sku)==(1,0) and allocated(db,lot)==0


def test_same_person_not_allowed_revoke_and_replay_requires_live_authorization():
    from test_pg_auto_stock_service import grant
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,1)
        value(db,d,org,loc,receipt)
        manager_grant=grant(db,d["intake"],org,loc,"MANAGER")
        doc=draft(db,d,org,loc,receipt)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc,actor=d["intake"])
        approved=approve(db,d,org,loc,doc)
        with pytest.raises(AutoStockConflict):
            approve(db,d,org,loc,doc,actor=d["intake"])
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (manager_grant,))
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,loc,doc,actor=d["intake"])
        grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
            WHERE user_id=%s AND organization_id=%s AND location_id=%s
              AND role_code='MANAGER' AND revoked_at IS NULL
            ORDER BY created_at DESC LIMIT 1""",(d["manager"],org,loc)).fetchone()[0]
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
        with pytest.raises(AutoStockDenied):
            approve(db,d,org,loc,doc)
        assert approved


def test_supplier_return_rollback_idempotency_and_concurrent_approval():
    d=_scenario()
    with psycopg.connect(DSN) as seed:
        org,loc,sku,lot,receipt=setup_receipt(seed,d,1)
        value(seed,d,org,loc,receipt)
        document=draft(seed,d,org,loc,receipt)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            approve(first,d,org,loc,document)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    approve(second,d,org,loc,document)
        with pytest.raises(AutoStockConflict):
            approve(second,d,org,loc,document)
        assert allocated(second,lot)==0 and balance(second,sku)==(1,0)


def test_owner_sql_pairing_rejects_unrelated_disposition():
    from roboport_api.auto_stock_disposition import dispose_stock_lot
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,receipt=setup_receipt(db,d,2)
        value(db,d,org,loc,receipt)
        doc=draft(db,d,org,loc,receipt,1)
        other=dispose_stock_lot(db,d["tokens"][d["intake"]],
            org,loc,lot,uuid4(),"WRITE_OFF",1,"Списание брака")
        valuation=db.execute("SELECT id FROM auto_stock_receipt_valuation WHERE document_id=%s",
                             (receipt,)).fetchone()[0]
        with pytest.raises(psycopg.errors.RaiseException,match="pairing"):
            with db.transaction():
                db.execute("SELECT set_config('roboport.supplier_return_actor',%s,true)",
                           (str(d["manager"]),))
                db.execute("""INSERT INTO auto_stock_supplier_return_acceptance(
                  id,request_id,document_id,disposition_id,source_valuation_id,
                  purchase_net_attributed_minor,purchase_vat_attributed_minor,accepted_by)
                  VALUES(%s,%s,%s,%s,%s,0,0,%s)""",
                  (uuid4(),uuid4(),doc,other,valuation,d["manager"]))
                db.execute("SET CONSTRAINTS ALL IMMEDIATE")
        assert db.execute("""SELECT count(*) FROM auto_stock_supplier_return_acceptance
            WHERE document_id=%s""",(doc,)).fetchone()[0]==0
