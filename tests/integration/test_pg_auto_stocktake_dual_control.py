"""PG17 stocktake maker/checker independence and actor provenance."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_lot_stocktake import count_stock_lot, approve_stock_lot_count
from roboport_api.auto_stock_service import AutoStockDenied, AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup, allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_service import grant


def test_independent_manager_approval_and_immutable_actor_provenance():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,3)
        grant(db,d["manager"],org,loc,"MANAGER")
        count_id=count_stock_lot(db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),2,"physical audit")
        assert db.execute("SELECT counted_by FROM auto_stock_lot_count WHERE id=%s",(count_id,)).fetchone()[0]==d["intake"]
        with pytest.raises(AutoStockDenied):
            approve_stock_lot_count(db,d["tokens"][d["intake"]],org,loc,count_id,uuid4())
        approval_id=approve_stock_lot_count(db,d["tokens"][d["manager"]],org,loc,count_id,uuid4())
        assert db.execute("SELECT approved_by,applied_delta FROM auto_stock_lot_count_approval WHERE id=%s",(approval_id,)).fetchone()==(d["manager"],2)
        assert allocated(db,lot)==2 and balance(db,item)==(5,0)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_stock_lot_count SET counted_by=%s WHERE id=%s",(d["manager"],count_id))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_stock_lot_count_approval SET approved_by=%s WHERE id=%s",(d["intake"],approval_id))


def test_same_actor_two_grants_cannot_self_approve_and_no_actor_direct_insert():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,2)
        grant(db,d["intake"],org,loc,"MANAGER")
        count_id=count_stock_lot(db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),1,"count notes")
        with pytest.raises(AutoStockConflict):
            approve_stock_lot_count(db,d["tokens"][d["intake"]],org,loc,count_id,uuid4())
        assert balance(db,item)==(2,0) and allocated(db,lot)==0
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_stock_lot_count
                    (id,request_id,lot_id,sku_id,expected,actual,reason,counted_by)
                    VALUES(%s,%s,%s,%s,0,1,'direct insert',%s)""",
                    (uuid4(),uuid4(),lot,item,d["manager"]))
        grant(db,d["manager"],org,loc,"MANAGER")
        with pytest.raises(RuntimeError):
            with db.transaction():
                approve_stock_lot_count(db,d["tokens"][d["manager"]],org,loc,count_id,uuid4())
                raise RuntimeError("rollback")
        assert balance(db,item)==(2,0) and allocated(db,lot)==0
        approval_id=approve_stock_lot_count(db,d["tokens"][d["manager"]],org,loc,count_id,uuid4())
        assert db.execute("SELECT approved_by FROM auto_stock_lot_count_approval WHERE id=%s",(approval_id,)).fetchone()[0]==d["manager"]


def test_manager_scope_and_revocation_fail_closed():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        count_id=count_stock_lot(db,d["tokens"][d["intake"]],org,loc,lot,uuid4(),0,"audit count")
        grant_id=grant(db,d["manager"],org,loc,"MANAGER")
        with pytest.raises(AutoStockDenied):
            approve_stock_lot_count(db,d["tokens"][d["manager"]],uuid4(),loc,count_id,uuid4())
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant_id,))
        with pytest.raises(AutoStockDenied):
            approve_stock_lot_count(db,d["tokens"][d["manager"]],org,loc,count_id,uuid4())
        assert balance(db,item)==(1,0)
