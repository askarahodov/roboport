"""PG17 warehouse reconciliation stays consistent across paired lot movement."""
from uuid import uuid4
import psycopg
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup


def snapshot(db,sku):
    return db.execute("""SELECT on_hand,reserved,lot_allocated,unallocated,
      available,discrepancy FROM auto_stock_reconciliation WHERE sku_id=%s""",
      (sku,)).fetchone()


def test_reconciliation_through_issue_and_return():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,3)
        assert snapshot(db,item)==(3,3,3,0,0,False)
        issue_work_order_from_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),2)
        assert snapshot(db,item)==(1,1,1,0,0,False)
        return_work_order_to_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),1)
        assert snapshot(db,item)==(2,1,2,0,1,False)


def test_reconciliation_tenant_scope_is_explicit():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,1)
        row=db.execute("""SELECT organization_id,location_id,article
          FROM auto_stock_reconciliation WHERE sku_id=%s""",(item,)).fetchone()
        assert row[0]==org and row[1]==loc and row[2]
        assert db.execute("""SELECT count(*) FROM auto_stock_reconciliation
          WHERE organization_id=%s AND location_id=%s AND sku_id=%s""",
          (uuid4(),loc,item)).fetchone()[0]==0
