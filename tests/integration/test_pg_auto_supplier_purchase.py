"""PG17 end-to-end offline supplier purchase → partial received lot → valuation.

Synthetic only. No external provider, no real purchasing/price authority.
"""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_supplier_purchase import (
    place_supplier_order,record_supplier_delivery,cancel_supplier_remainder,
    list_supplier_orders,
)
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from roboport_api.auto_stock_receipt_document import (
    accept_receipt_document,
)
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated
from test_pg_auto_work_order_stock import work_order
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def quotes(db,sku_id,qty=10,price=1200):
    article=db.execute("SELECT article FROM auto_stock_sku WHERE id=%s",(sku_id,)).fetchone()[0]
    now=datetime.now(timezone.utc).replace(microsecond=0)
    return [{
        "offer_id":f"OFFLINE-{n}","supplier_id":f"SYNTH-SUP-{n}",
        "source_revision":f"REV-{n}","quoted_at":now.isoformat(),
        "expires_at":(now+timedelta(days=7)).isoformat(),"currency":"RUB",
        "unit_purchase_minor":price+(n-1)*100,
        "available_quantity":qty,"article":article,
        "delivery_days":n,
    } for n in (1,2)]


def environment(qty=1):
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,warehouse_grant=setup(db,d,qty)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
        bay=db.execute("SELECT id FROM auto_work_bay WHERE organization_id=%s AND location_id=%s",
                       (org,loc)).fetchone()[0]
        order,_=work_order(db,d,org,loc,bay)
        offers=quotes(db,item)
    return d,org,loc,item,lot,order,offers,warehouse_grant,manager_grant


def create(db,d,org,loc,order,lot,offers,desired=5,key=None,actor=None):
    return place_supplier_order(
        db,d["tokens"][actor or d["intake"]],org,loc,order,lot,
        key or uuid4(),desired,offers,"SYNTH-SUP-1","OFFLINE-1",
    )


def delivery(db,d,org,loc,purchase,qty=1,key=None,ref="OFFLINE-DELIVERY-A",actor=None):
    return record_supplier_delivery(
        db,d["tokens"][actor or d["intake"]],org,loc,purchase,key or uuid4(),
        qty,ref,
    )


def test_shortage_order_two_partial_receipts_and_real_stock_posting():
    d,org,loc,item,lot,order,offers,_,_=environment(1)
    with psycopg.connect(DSN) as db:
        request=uuid4()
        purchase,shortage=create(db,d,org,loc,order,lot,offers,5,request)
        assert shortage==4
        assert create(db,d,org,loc,order,lot,offers,5,request)==(purchase,4)
        assert balance(db,item)==(1,0)
        with pytest.raises(AutoStockConflict):
            create(db,d,org,loc,order,lot,offers,6,request)
        with pytest.raises(AutoStockConflict):
            create(db,d,org,loc,order,lot,offers,5)
        first_key=uuid4()
        a,first=delivery(db,d,org,loc,purchase,2,first_key)
        assert delivery(db,d,org,loc,purchase,2,first_key)==(a,first)
        with pytest.raises(AutoStockConflict):
            delivery(db,d,org,loc,purchase,1,first_key)
        assert balance(db,item)==(1,0), "draft may not post stock"
        approval=accept_receipt_document(db,d["tokens"][d["manager"]],
                                         org,loc,first,uuid4())
        assert approval
        value_id=record_receipt_valuation(
            db,d["tokens"][d["manager"]],org,loc,first,uuid4(),
            "RUB",offers[0]["unit_purchase_minor"],240,
            offers[0]["source_revision"],
            datetime.fromisoformat(offers[0]["quoted_at"]))
        assert value_id
        second_id,second=delivery(db,d,org,loc,purchase,2,uuid4(),"OFFLINE-DELIVERY-B")
        assert second_id!=a
        accept_receipt_document(db,d["tokens"][d["manager"]],
                                org,loc,second,uuid4())
        record_receipt_valuation(db,d["tokens"][d["manager"]],
                                 org,loc,second,uuid4(),"RUB",
                                 offers[0]["unit_purchase_minor"],240,
                                 offers[0]["source_revision"],
                                 datetime.fromisoformat(offers[0]["quoted_at"]))
        assert balance(db,item)==(5,0) and allocated(db,lot)==4
        rows,next_id=list_supplier_orders(db,d["tokens"][d["intake"]],org,loc)
        assert next_id is None and len(rows)==1
        assert rows[0]["orderId"]==str(purchase)
        assert rows[0]["orderedQuantity"]==4
        assert rows[0]["documentedQuantity"]==4
        assert rows[0]["acceptedQuantity"]==4
        assert rows[0]["valuedQuantity"]==4
        assert rows[0]["outstandingQuantity"]==0
        assert rows[0]["purchaseUnitMinor"]==1200
        with pytest.raises(AutoStockConflict):
            delivery(db,d,org,loc,purchase,1)
        with pytest.raises(AutoStockConflict):
            cancel_supplier_remainder(db,d["tokens"][d["intake"]],
                                      org,loc,purchase,uuid4())
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_supplier_purchase_order SET purchase_unit_minor=1 WHERE id=%s",
                           (purchase,))


def test_cancel_only_remaining_and_retry_without_losing_receipt():
    d,org,loc,item,lot,order,offers,_,_=environment(1)
    with psycopg.connect(DSN) as db:
        purchase,qty=create(db,d,org,loc,order,lot,offers,5)
        assert qty==4
        event,document=delivery(db,d,org,loc,purchase,1)
        key=uuid4()
        cancel,canceled=cancel_supplier_remainder(
            db,d["tokens"][d["intake"]],org,loc,purchase,key)
        assert canceled==3
        assert cancel_supplier_remainder(db,d["tokens"][d["intake"]],
                    org,loc,purchase,key)==(cancel,3)
        with pytest.raises(AutoStockConflict):
            cancel_supplier_remainder(db,d["tokens"][d["intake"]],
                                      org,loc,purchase,uuid4())
        with pytest.raises(AutoStockConflict):
            delivery(db,d,org,loc,purchase,1)
        assert balance(db,item)==(1,0)
        accept_receipt_document(db,d["tokens"][d["manager"]],
                                org,loc,document,uuid4())
        assert balance(db,item)==(2,0)
        rows,_=list_supplier_orders(db,d["tokens"][d["manager"]],org,loc)
        assert rows[0]["canceledQuantity"]==3
        assert rows[0]["documentedQuantity"]==1
        assert rows[0]["acceptedQuantity"]==1
        assert rows[0]["outstandingQuantity"]==0
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_supplier_purchase_cancel WHERE id=%s",(cancel,))


def test_no_shortage_untrusted_quote_scope_and_revoke():
    d,org,loc,item,lot,order,offers,grant_id,_=environment(3)
    with psycopg.connect(DSN) as db:
        with pytest.raises(AutoStockConflict):
            create(db,d,org,loc,order,lot,offers,2)
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,lot,offers,5,
                   actor=d["manager"])
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,lot,offers,5,
                   actor=d["client_a"])
        with pytest.raises(AutoStockDenied):
            create(db,d,org,uuid4(),order,lot,offers,5)
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,uuid4(),offers,5)
        bad=[dict(x) for x in offers]
        bad[0]["article"]="FOREIGN-PART"
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,lot,bad,5)
        bad=[dict(x) for x in offers]
        bad[0]["available_quantity"]=1
        with pytest.raises(AutoStockConflict):
            create(db,d,org,loc,order,lot,bad,5)
        bad=[dict(x) for x in offers]
        bad[0]["delivery_days"]=367
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,lot,bad,5)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
        with pytest.raises(AutoStockDenied):
            create(db,d,org,loc,order,lot,offers,5)
        with pytest.raises(AutoStockDenied):
            list_supplier_orders(db,d["tokens"][d["intake"]],org,loc)


def test_two_partial_receipts_serialize_under_purchase_row_lock():
    d,org,loc,item,lot,order,offers,_,_=environment(0)
    with psycopg.connect(DSN) as db:
        purchase,_=create(db,d,org,loc,order,lot,offers,3)
    with psycopg.connect(DSN) as one,psycopg.connect(DSN) as two:
        with one.transaction():
            delivery(one,d,org,loc,purchase,2)
            two.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with two.transaction():
                    delivery(two,d,org,loc,purchase,1)
        with pytest.raises(AutoStockConflict):
            delivery(two,d,org,loc,purchase,1)
        assert one.execute("SELECT count(*) FROM auto_supplier_purchase_receipt WHERE purchase_id=%s",
                           (purchase,)).fetchone()[0]==1


def test_transaction_rolls_back_purchase_and_pending_receipt_together():
    d,org,loc,item,lot,order,offers,_,_=environment(0)
    with psycopg.connect(DSN) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                purchase,_=create(db,d,org,loc,order,lot,offers,4)
                delivery(db,d,org,loc,purchase,2)
                raise RuntimeError("disposable rollback")
        assert db.execute("SELECT count(*) FROM auto_supplier_purchase_order WHERE work_order_id=%s",
                          (order,)).fetchone()[0]==0
        assert db.execute("""SELECT count(*) FROM auto_stock_receipt_document
                            WHERE lot_id=%s""",(lot,)).fetchone()[0]==0
