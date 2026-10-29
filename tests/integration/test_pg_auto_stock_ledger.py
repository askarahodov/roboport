"""PG17 automotive stock ledger: atomic, append-only, tenant isolated."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay


def sku(db, org, location):
    item = uuid4()
    db.execute(
        "INSERT INTO auto_stock_sku(id,organization_id,location_id,article) VALUES(%s,%s,%s,%s)",
        (item,org,location,"SYN-" + item.hex[:8]),
    )
    return item


def record(db, item, action, quantity, key=None):
    return db.execute(
        "SELECT record_auto_stock(%s,%s,%s,%s)",
        (item,key or uuid4(),action,quantity),
    ).fetchone()[0]


def balance(db, item):
    return db.execute(
        "SELECT on_hand,reserved FROM auto_stock_balance WHERE sku_id=%s",(item,),
    ).fetchone()


def test_receipt_reserve_release_and_immutable_history():
    with psycopg.connect(DSN) as db:
        org,loc,_ = _org_and_bay(db)
        item = sku(db,org,loc)
        assert balance(db,item) == (0,0)
        record(db,item,"RECEIPT",5)
        assert balance(db,item) == (5,0)
        key=uuid4()
        first=record(db,item,"RESERVE",3,key)
        assert record(db,item,"RESERVE",3,key)==first
        assert balance(db,item)==(5,3)
        record(db,item,"RELEASE",2)
        assert balance(db,item)==(5,1)
        assert db.execute("SELECT count(*) FROM auto_stock_event WHERE sku_id=%s",(item,)).fetchone()[0]==3
        for sql in (
            "DELETE FROM auto_stock_event WHERE sku_id=%s",
            "UPDATE auto_stock_event SET quantity=2 WHERE sku_id=%s",
            "DELETE FROM auto_stock_sku WHERE id=%s",
            "UPDATE auto_stock_sku SET article='OTHER' WHERE id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,(item,))


def test_insufficient_stock_rollback_and_replay_mismatch():
    with psycopg.connect(DSN) as db:
        org,loc,_ = _org_and_bay(db)
        item=sku(db,org,loc)
        record(db,item,"RECEIPT",2)
        key=uuid4()
        record(db,item,"RESERVE",2,key)
        for action,qty in (("RESERVE",1),("RELEASE",3),("INVALID",1),("RECEIPT",0)):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    record(db,item,action,qty)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                record(db,item,"RESERVE",1,key)
        assert balance(db,item)==(2,2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                record(db,item,"RELEASE",1)
                raise RuntimeError("rollback")
        assert balance(db,item)==(2,2)


def test_cross_tenant_scope_cannot_attach_stock_event_or_sku():
    with psycopg.connect(DSN) as db:
        a,loc_a,_=_org_and_bay(db)
        b,loc_b,_=_org_and_bay(db)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                sku(db,a,loc_b)
        item=sku(db,a,loc_a)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_event
                  (id,request_id,sku_id,organization_id,location_id,action,quantity)
                  VALUES(%s,%s,%s,%s,%s,'RECEIPT',1)""",
                  (uuid4(),uuid4(),item,b,loc_b))
        assert balance(db,item)==(0,0)


def test_direct_insert_cannot_create_negative_balance():
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        item=sku(db,org,loc)
        for action in ("RESERVE","RELEASE"):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_stock_event
                        (id,request_id,sku_id,organization_id,location_id,action,quantity)
                        VALUES(%s,%s,%s,%s,%s,%s,1)""",
                        (uuid4(),uuid4(),item,org,loc,action))
        assert balance(db,item)==(0,0)


def test_concurrent_reserve_is_serialized():
    with psycopg.connect(DSN) as setup:
        org,loc,_=_org_and_bay(setup)
        item=sku(setup,org,loc)
        record(setup,item,"RECEIPT",1)
    with psycopg.connect(DSN) as one,psycopg.connect(DSN) as two:
        with one.transaction():
            record(one,item,"RESERVE",1)
            two.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with two.transaction():
                    record(two,item,"RESERVE",1)
        with pytest.raises(psycopg.errors.RaiseException):
            with two.transaction():
                record(two,item,"RESERVE",1)
        assert balance(two,item)==(1,1)
