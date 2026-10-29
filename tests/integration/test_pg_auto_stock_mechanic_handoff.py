"""Mechanic handoff PG17: scoped, immutable, replay-safe acknowledgement."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_mechanic_handoff import acknowledge_mechanic_handoff
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance


def test_mechanic_ack_replay_denied_scope_and_immutable():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,3)
        mechanic_grant=grant(db,d["manager"],org,loc,"MECHANIC")
        issue=issue_work_order_from_lot(db,d["tokens"][d["intake"]],
                                       org,loc,order,lot,uuid4(),2)
        key=uuid4()
        with pytest.raises(AutoStockDenied):
            acknowledge_mechanic_handoff(db,d["tokens"][d["intake"]],
                org,loc,issue,key)
        with pytest.raises(AutoStockDenied):
            acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,uuid4(),issue,key)
        ack=acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,loc,issue,key)
        assert acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,loc,issue,key)==ack
        assert db.execute("""SELECT received_by,issue_id
          FROM auto_stock_mechanic_handoff WHERE id=%s""",(ack,)).fetchone()==(
          d["manager"],issue)
        assert balance(db,sku)==(1,1)
        with pytest.raises(AutoStockConflict):
            acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,loc,issue,uuid4())
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_mechanic_handoff WHERE id=%s",(ack,))
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (mechanic_grant,))
        with pytest.raises(AutoStockDenied):
            acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,loc,issue,key)


def test_mechanic_ack_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,2)
        grant(db,d["manager"],org,loc,"MECHANIC")
        issue=issue_work_order_from_lot(db,d["tokens"][d["intake"]],
                                       org,loc,order,lot,uuid4(),1)
        with pytest.raises(RuntimeError):
            with db.transaction():
                acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                                             org,loc,issue,uuid4())
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_stock_mechanic_handoff WHERE issue_id=%s",
                          (issue,)).fetchone()[0]==0
