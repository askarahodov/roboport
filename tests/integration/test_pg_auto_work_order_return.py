"""PG17 returns to a particular automobile work order: immutable balanced journal."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_work_order_return import return_work_order_stock
from roboport_api.auto_stock_service import AutoStockConflict, AutoStockDenied
from test_pg_auto_work_order_stock import work_order, act
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku, balance, record
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def ret(db, d, org, loc, order, item, count, key=None, actor=None):
    return return_work_order_stock(
        db, d["tokens"][actor or d["intake"]], org, loc,
        order, item, key or uuid4(), count,
    )


def test_return_reconciles_issued_quantity_without_creating_reservation():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        order, _ = work_order(db, d, org, loc, bay)
        item = sku(db, org, loc)
        grant(db, d["intake"], org, loc)
        record(db, item, "RECEIPT", 5)
        act(db,d,org,loc,order,item,"RESERVE",4)
        act(db,d,org,loc,order,item,"ISSUE",3)
        assert balance(db,item) == (2,1)
        key = uuid4()
        returned = ret(db,d,org,loc,order,item,2,key)
        assert ret(db,d,org,loc,order,item,2,key) == returned
        assert balance(db,item) == (4,1)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,item,2)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,item,1,key)
        ret(db,d,org,loc,order,item,1)
        assert balance(db,item) == (5,1)
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,item,1)
        assert db.execute(
            """SELECT count(*) FROM auto_work_order_stock_event
               WHERE work_order_id=%s AND action='RETURN'""",
            (order,),
        ).fetchone()[0] == 2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_work_order_stock_event SET quantity=5 WHERE stock_event_id=%s",
                    (returned,),
                )


def test_cross_tenant_role_revocation_and_canceled_booking():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        foreign,otherloc,_=_org_and_bay(db)
        order,booking=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        other=sku(db,foreign,otherloc)
        grant_id=grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",2)
        act(db,d,org,loc,order,item,"RESERVE",1)
        act(db,d,org,loc,order,item,"ISSUE",1)
        with pytest.raises(AutoStockDenied):
            ret(db,d,foreign,otherloc,order,other,1)
        with pytest.raises(AutoStockDenied):
            ret(db,d,org,loc,order,other,1)
        with pytest.raises(AutoStockDenied):
            ret(db,d,org,loc,order,item,1,actor=d["manager"])
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        with pytest.raises(AutoStockConflict):
            ret(db,d,org,loc,order,item,1)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            ret(db,d,org,loc,order,item,1)
        assert balance(db,item)==(1,0)


def test_direct_return_without_matching_order_event_cannot_commit():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        order,_=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_event
                  (id,request_id,sku_id,organization_id,location_id,action,quantity)
                  VALUES(%s,%s,%s,%s,%s,'RETURN',1)""",
                  (uuid4(),uuid4(),item,org,loc))
                db.execute("SET CONSTRAINTS auto_stock_return_link IMMEDIATE")
        assert balance(db,item)==(0,0)


def test_concurrent_return_locks_sku_and_rejects_over_return():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        order,_=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",1)
        act(db,d,org,loc,order,item,"RESERVE",1)
        act(db,d,org,loc,order,item,"ISSUE",1)
    with psycopg.connect(DSN) as one, psycopg.connect(DSN) as two:
        with one.transaction():
            ret(one,d,org,loc,order,item,1)
            two.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with two.transaction():
                    ret(two,d,org,loc,order,item,1)
        with pytest.raises(AutoStockConflict):
            ret(two,d,org,loc,order,item,1)
        assert balance(two,item)==(1,0)


def test_transaction_rollback_keeps_pairing_and_stock_unmodified():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        order,_=work_order(db,d,org,loc,bay)
        item=sku(db,org,loc)
        grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",2)
        act(db,d,org,loc,order,item,"RESERVE",1)
        act(db,d,org,loc,order,item,"ISSUE",1)
        with pytest.raises(RuntimeError):
            with db.transaction():
                ret(db,d,org,loc,order,item,1)
                raise RuntimeError("simulate rollback")
        assert balance(db,item)==(1,0)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_stock_event WHERE work_order_id=%s AND action='RETURN'",
            (order,),
        ).fetchone()[0]==0
