"""PG17 + real HTTPS: supplier shortage follows current booking after reschedule.

Prove both app-level scope AND the independent SQL INSERT guard. All records
are synthetic, no real supplier deliveries, stock valuation or money.
"""
from datetime import timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_appointment_service import (
    reschedule_appointment, current_appointment_booking,
)
from roboport_api.auto_supplier_purchase import place_supplier_order
from roboport_api.auto_stock_service import AutoStockDenied, AutoStockConflict
from test_pg_auto_supplier_purchase_http import fixture, post
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def move(db, d, org, loc, order):
    row = db.execute("""
        SELECT a.id,b.id,b.bay_id,b.starts_at,b.ends_at
        FROM auto_work_order w
        JOIN auto_appointment a ON a.id=w.appointment_id
        JOIN auto_bay_booking b ON b.id=a.booking_id
        WHERE w.id=%s
    """, (order,)).fetchone()
    assert row is not None
    appointment, original, bay, starts, ends = row
    # The real rescheduler needs a live scoped INTAKE role in addition to
    # the WAREHOUSE grant used by supplier ordering.
    grant(db, d["intake"], org, loc, "INTAKE")
    fresh = reschedule_appointment(
        db, d["tokens"][d["intake"]], org, appointment, bay,
        starts + timedelta(hours=2), ends + timedelta(hours=2),
        request_key=uuid4(),
    )
    assert fresh != original
    assert current_appointment_booking(db, appointment) == fresh
    assert db.execute(
        "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",
        (original,),
    ).fetchone() == (True,)
    assert db.execute(
        "SELECT canceled_at FROM auto_bay_booking WHERE id=%s",
        (fresh,),
    ).fetchone()[0] is None
    return appointment, original, fresh


def test_reschedule_allows_scoped_order_and_partial_receipt_http(monkeypatch):
    d, org, loc, sku, lot, order, offers, _, _, endpoint, body = fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        appointment, original, current = move(db, d, org, loc, order)

    with TestClient(app, base_url="https://127.0.0.1") as http:
        key=uuid4()
        created=post(http,d,d["intake"],endpoint,body,key)
        assert created.status_code==201,created.text
        ident=created.json()["orderId"]
        assert created.json()["orderedQuantity"]==4
        assert post(http,d,d["intake"],endpoint,body,key).json()==created.json()
        assert post(http,d,d["intake"],endpoint,{**body,"desiredQuantity":6},
                    key).status_code==409
        # The purchase line stays linked to the immutable original WorkOrder,
        # not the old canceled booking. Supplier delivery is only a draft.
        partial=post(http,d,d["intake"],endpoint+"/"+ident+"/receipts",{
            "locationId":str(loc),
            "quantity":2,
            "supplierReference":"SYNTH-RESCHEDULE-DELIVERY",
        })
        assert partial.status_code==201,partial.text
        assert partial.json()["syntheticOnly"] is True
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        listed=http.get(endpoint,params={"locationId":str(loc)})
        assert listed.status_code==200,listed.text
        assert listed.json()["items"][0]["workOrderId"]==str(order)
        assert listed.json()["items"][0]["documentedQuantity"]==2
        assert listed.json()["items"][0]["outstandingQuantity"]==2
        with psycopg.connect(DSN) as db:
            assert db.execute("""
                SELECT count(*) FROM auto_supplier_purchase_order
                WHERE work_order_id=%s AND organization_id=%s AND location_id=%s
            """,(order,org,loc)).fetchone()==(1,)
            assert db.execute("""
                SELECT sum(quantity) FROM auto_supplier_purchase_receipt
                WHERE purchase_id=%s
            """,(UUID(ident),)).fetchone()==(2,)
            assert current_appointment_booking(db,appointment)==current
            assert db.execute(
                "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",
                (original,),
            ).fetchone()==(True,)


def test_current_booking_cancel_denies_new_purchase_even_with_live_old_record(monkeypatch):
    d, org, loc, sku, lot, order, offers, _, _, endpoint, body = fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        appointment, original, current = move(db,d,org,loc,order)
        db.execute(
            "UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
            (current,),
        )
        with pytest.raises(AutoStockDenied):
            place_supplier_order(db,d["tokens"][d["intake"]],org,loc,
                                 order,lot,uuid4(),5,offers,
                                 "SYNTH-SUP-1","OFFLINE-1")
        assert db.execute("""
            SELECT count(*) FROM auto_supplier_purchase_order
            WHERE work_order_id=%s
        """,(order,)).fetchone()==(0,)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        assert post(http,d,d["intake"],endpoint,body).status_code==403


def test_db_guard_validates_rescheduled_scope_not_only_python(monkeypatch):
    d, org, loc, sku, lot, order, offers, _, _, endpoint, body = fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        _, original, current = move(db,d,org,loc,order)
        # Trigger is exercised by the HTTP insert in the primary test.
        # Independently assert that a disabled *current* booking blocks the
        # transaction, despite the original immutable appointment identity.
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
                   (current,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("SELECT set_config('roboport.purchase_actor',%s,true)",
                           (str(d["intake"]),))
                db.execute("""
                    INSERT INTO auto_supplier_purchase_order(
                      id,request_id,organization_id,location_id,work_order_id,
                      sku_id,lot_id,desired_quantity,available_at_order,
                      ordered_quantity,offers,selected_supplier,selected_offer,
                      quote_revision,quoted_at,expires_at,currency,
                      purchase_unit_minor,created_by
                    )
                    SELECT %s,%s,%s,%s,%s,%s,%s,5,1,4,
                           %s::jsonb,'SYNTH-SUP-1','OFFLINE-1',
                           %s,%s,%s,'RUB',1200,%s
                """,(uuid4(),uuid4(),org,loc,order,sku,lot,
                      __import__("json").dumps(offers),
                      offers[0]["source_revision"],offers[0]["quoted_at"],
                      offers[0]["expires_at"],d["intake"]))
