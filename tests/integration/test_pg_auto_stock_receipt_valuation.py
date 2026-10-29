"""PG17 receipt cost and VAT provenance is independent of physical balance."""
from datetime import datetime,timezone
from uuid import uuid4

import psycopg
import pytest
from roboport_api.auto_stock_receipt_document import (
    create_receipt_document,accept_receipt_document)
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant


def setup_receipt(db,d,quantity=3,accept=True):
    org,loc,item,lot,_=setup(db,d,1)
    grant(db,d["manager"],org,loc,"MANAGER")
    doc=create_receipt_document(db,d["tokens"][d["intake"]],
         org,loc,lot,uuid4(),quantity,"SUPPLIER-DOC-1")
    if accept:
        accept_receipt_document(db,d["tokens"][d["manager"]],
            org,loc,doc,uuid4())
    return org,loc,item,lot,doc


def value(db,d,org,loc,doc,key=None,unit=1250,vat=600,actor=None):
    return record_receipt_valuation(db,d["tokens"][actor or d["manager"]],
        org,loc,doc,key or uuid4(),"RUB",unit,vat,
        "OFFER-REVISION-1",datetime(2026,10,28,12,0,tzinfo=timezone.utc))


def test_purchase_valuation_audit_and_distinct_value_balance():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,doc=setup_receipt(db,d,3)
        assert balance(db,item)==(4,0) and allocated(db,lot)==3
        request=uuid4()
        v=value(db,d,org,loc,doc,request)
        assert value(db,d,org,loc,doc,request)==v
        row=db.execute("""SELECT currency,purchase_unit_net_minor,purchase_net_total_minor,
              purchase_vat_total_minor,purchase_gross_total_minor,
              supplier_price_revision,receipt_source_reference,valued_by
              FROM auto_stock_receipt_valuation_report
              WHERE valuation_id=%s""",(v,)).fetchone()
        assert row==("RUB",1250,3750,600,4350,
                   "OFFER-REVISION-1","SUPPLIER-DOC-1",d["manager"])
        assert balance(db,item)==(4,0) and allocated(db,lot)==3
        with pytest.raises(AutoStockConflict):
            value(db,d,org,loc,doc,request,unit=1400)
        with pytest.raises(AutoStockConflict):
            value(db,d,org,loc,doc)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_receipt_valuation WHERE id=%s",(v,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_stock_receipt_valuation SET purchase_vat_total_minor=1 WHERE id=%s",(v,))


def test_unaccepted_receipt_and_invalid_price_never_touch_stock():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,doc=setup_receipt(db,d,3,accept=False)
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc)
        assert balance(db,item)==(1,0) and allocated(db,lot)==0
        accept_receipt_document(db,d["tokens"][d["manager"]],
                               org,loc,doc,uuid4())
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc,unit=-1)
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc,unit=True)
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc,vat=-1)
        with pytest.raises(AutoStockDenied):
            value(db,d,org,uuid4(),doc)
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc,actor=d["intake"])
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_receipt_valuation
                  (id,request_id,document_id,currency,purchase_unit_net_minor,
                   purchase_vat_total_minor,supplier_price_revision,
                   supplier_quoted_at,valued_by)
                  VALUES(%s,%s,%s,'RUB',100,20,'QUOTE-1',now(),%s)""",
                  (uuid4(),uuid4(),doc,d["intake"]))
        assert balance(db,item)==(4,0)


def test_valuation_revoke_and_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,doc=setup_receipt(db,d,2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                value(db,d,org,loc,doc)
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_stock_receipt_valuation WHERE document_id=%s",
                          (doc,)).fetchone()[0]==0
        grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
          WHERE user_id=%s AND organization_id=%s AND location_id=%s
            AND role='MANAGER' AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1""",
            (d["manager"],org,loc)).fetchone()[0]
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            value(db,d,org,loc,doc)
        assert db.execute("SELECT count(*) FROM auto_stock_receipt_valuation WHERE document_id=%s",
                          (doc,)).fetchone()[0]==0
