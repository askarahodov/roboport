"""PG17 end-to-end internal work-order stock accounting with synthetic identities."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_auto_stock_ledger import sku, balance, record
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def work_order(db, d, org, loc, bay):
    booking = _book(db, org, loc, bay, "2035-02-01 09:00+00", "2035-02-01 10:00+00")
    db.execute(
        "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
        (d["manager"], d["link"]),
    )
    appt, order = uuid4(), uuid4()
    db.execute("""INSERT INTO auto_appointment
       (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
       VALUES(%s,%s,%s,%s,%s,%s)""",
       (appt,booking,org,loc,d["vehicle"],d["link"]))
    db.execute("""INSERT INTO auto_work_order
       (id,appointment_id,organization_id,location_id) VALUES(%s,%s,%s,%s)""",
       (order,appt,org,loc))
    return order,booking


def act(db,d,org,loc,order,item,action,quantity,key=None,actor=None):
    return mutate_work_order_stock(
        db,d["tokens"][actor or d["intake"]],org,loc,order,item,
        key or uuid4(),action,quantity,
    )


def test_issue_consumes_both_reserved_and_on_hand_and_replay():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        order,_=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",5)
        key=uuid4()
        reserved=act(db,d,org,loc,order,item,"RESERVE",4,key)
        assert act(db,d,org,loc,order,item,"RESERVE",4,key)==reserved
        assert balance(db,item)==(5,4)
        consumed=act(db,d,org,loc,order,item,"ISSUE",3)
        assert consumed!=reserved
        assert balance(db,item)==(2,1)
        act(db,d,org,loc,order,item,"RELEASE",1)
        assert balance(db,item)==(2,0)
        assert db.execute(
            "SELECT action,quantity FROM auto_work_order_stock_event WHERE work_order_id=%s ORDER BY created_at,action",
            (order,),
        ).fetchall()
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,order,item,"ISSUE",1)
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,order,item,"RELEASE",1)
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,order,item,"ISSUE",1,key)
        assert balance(db,item)==(2,0)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_stock_event WHERE stock_event_id=%s",(consumed,))


def test_foreign_order_and_role_denied_and_closed_booking():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        other,foreign_loc,_=_org_and_bay(db)
        order,booking=work_order(db,d,org,loc,bay)
        local_item=sku(db,org,loc)
        foreign_item=sku(db,other,foreign_loc)
        record(db,local_item,"RECEIPT",3)
        grant_id=grant(db,d["intake"],org,loc)
        with pytest.raises(AutoStockDenied):
            act(db,d,other,foreign_loc,order,foreign_item,"RESERVE",1)
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,order,foreign_item,"RESERVE",1)
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,order,local_item,"RESERVE",1,actor=d["manager"])
        act(db,d,org,loc,order,local_item,"RESERVE",1)
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        with pytest.raises(AutoStockConflict):
            act(db,d,org,loc,order,local_item,"ISSUE",1)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            act(db,d,org,loc,order,local_item,"ISSUE",1)


def test_concurrent_issue_serializes_on_sku_and_never_double_consumes():
    d=_scenario()
    with psycopg.connect(DSN) as setup:
        org,loc,bay=_org_and_bay(setup)
        order,_=work_order(setup,d,org,loc,bay)
        item=sku(setup,org,loc)
        grant(setup,d["intake"],org,loc)
        record(setup,item,"RECEIPT",1)
        act(setup,d,org,loc,order,item,"RESERVE",1)
    with psycopg.connect(DSN) as one,psycopg.connect(DSN) as two:
        with one.transaction():
            act(one,d,org,loc,order,item,"ISSUE",1)
            two.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with two.transaction():
                    act(two,d,org,loc,order,item,"ISSUE",1)
        with pytest.raises(AutoStockConflict):
            act(two,d,org,loc,order,item,"ISSUE",1)
        assert balance(two,item)==(0,0)


def test_failed_issue_rollback_no_phantom_consumption():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        order,_=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",2)
        act(db,d,org,loc,order,item,"RESERVE",2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                act(db,d,org,loc,order,item,"ISSUE",1)
                raise RuntimeError("transaction rollback")
        assert balance(db,item)==(2,2)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_stock_event WHERE work_order_id=%s AND action='ISSUE'",
            (order,),
        ).fetchone()[0]==0
