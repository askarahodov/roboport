"""A returned shipment cannot subsequently be acknowledged as outstanding."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from roboport_api.auto_stock_mechanic_handoff import acknowledge_mechanic_handoff
from roboport_api.auto_stock_service import AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup
from test_pg_auto_stock_service import grant


def test_fully_returned_issue_cannot_be_acknowledged():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,2)
        grant(db,d["manager"],org,loc,"MECHANIC")
        issue=issue_work_order_from_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),2)
        return_work_order_to_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),2)
        with pytest.raises(AutoStockConflict):
            acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
                org,loc,issue,uuid4())
        assert db.execute("SELECT count(*) FROM auto_stock_mechanic_handoff WHERE issue_id=%s",
                          (issue,)).fetchone()[0]==0


def test_partially_returned_issue_can_still_be_acknowledged():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,sku,lot,_=setup(db,d,3)
        grant(db,d["manager"],org,loc,"MECHANIC")
        issue=issue_work_order_from_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),3)
        return_work_order_to_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),1)
        ack=acknowledge_mechanic_handoff(db,d["tokens"][d["manager"]],
            org,loc,issue,uuid4())
        assert db.execute("SELECT issue_id FROM auto_stock_mechanic_handoff WHERE id=%s",
                          (ack,)).fetchone()[0]==issue
