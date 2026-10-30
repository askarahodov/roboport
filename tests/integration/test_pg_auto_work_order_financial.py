"""PG17 synthetic WorkOrder charges/credits: tenant scope, immutability, idempotency."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_work_order_stock import work_order
from test_pg_baseline import DSN


def add(db, order, key, action, amount, reason="synthetic estimate"):
    return db.execute("SELECT record_auto_work_order_financial(%s,%s,%s,%s,%s)",
                      (order,key,action,amount,reason)).fetchone()[0]


def test_financial_charge_credit_replay_and_immutable_scope():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other_org, other_loc, _ = _org_and_bay(db)
        order, booking = work_order(db,d,org,loc,bay)
        key = uuid4()
        event_id = add(db,order,key,"CHARGE",12500)
        assert add(db,order,key,"CHARGE",12500) == event_id
        assert db.execute(
            "SELECT balance_minor FROM auto_work_order_financial_balance WHERE work_order_id=%s",
            (order,)).fetchone() == (12500,)
        add(db,order,uuid4(),"CREDIT",2500,"estimate correction")
        assert db.execute(
            "SELECT balance_minor FROM auto_work_order_financial_balance WHERE work_order_id=%s",
            (order,)).fetchone() == (10000,)
        for action,amount in (("CREDIT",12500),("CHARGE",0),("PAYMENT",100)):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    add(db,order,uuid4(),action,amount)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add(db,order,key,"CHARGE",15000)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_financial_event
                    (id,request_id,work_order_id,organization_id,location_id,action,amount_minor,reason)
                    VALUES(%s,%s,%s,%s,%s,'CHARGE',500,'foreign organization')""",
                    (uuid4(),uuid4(),order,other_org,other_loc))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order_financial_event SET amount_minor=1 WHERE id=%s",(event_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_financial_event WHERE id=%s",(event_id,))
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add(db,order,uuid4(),"CHARGE",100)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_financial_event WHERE work_order_id=%s",
            (order,)).fetchone() == (2,)


def test_financial_failed_transaction_rolls_back_all_movements():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay = _org_and_bay(db)
        order,_ = work_order(db,d,org,loc,bay)
        with pytest.raises(RuntimeError):
            with db.transaction():
                add(db,order,uuid4(),"CHARGE",999)
                raise RuntimeError("rollback")
        assert db.execute(
            "SELECT balance_minor FROM auto_work_order_financial_balance WHERE work_order_id=%s",
            (order,)).fetchone() == (0,)
