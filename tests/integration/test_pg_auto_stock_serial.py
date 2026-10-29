"""PG17 serial registration: tenant, unique identity, capacity and replay."""
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_stock_serial import register_lot_serial
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup


def test_serial_scoped_unique_capacity_replay_and_immutable():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,role=setup(db,d,2)
        key=uuid4()
        serial=register_lot_serial(db,d["tokens"][d["intake"]],
             org,loc,lot,key,"SN-UNIT-001")
        assert register_lot_serial(db,d["tokens"][d["intake"]],
             org,loc,lot,key,"SN-UNIT-001")==serial
        assert db.execute("SELECT serial_number,registered_by FROM auto_stock_serial WHERE id=%s",
            (serial,)).fetchone()==("SN-UNIT-001",d["intake"])
        with pytest.raises(AutoStockConflict):
            register_lot_serial(db,d["tokens"][d["intake"]],
                org,loc,lot,key,"SN-UNIT-002")
        with pytest.raises(AutoStockConflict):
            register_lot_serial(db,d["tokens"][d["intake"]],
                org,loc,lot,uuid4(),"SN-UNIT-001")
        register_lot_serial(db,d["tokens"][d["intake"]],
             org,loc,lot,uuid4(),"SN-UNIT-002")
        with pytest.raises(AutoStockConflict):
            register_lot_serial(db,d["tokens"][d["intake"]],
                 org,loc,lot,uuid4(),"SN-UNIT-003")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_serial WHERE id=%s",(serial,))
        with pytest.raises(AutoStockDenied):
            register_lot_serial(db,d["tokens"][d["manager"]],
                org,loc,lot,uuid4(),"SN-UNIT-004")
        with pytest.raises(AutoStockDenied):
            register_lot_serial(db,d["tokens"][d["intake"]],
                uuid4(),loc,lot,uuid4(),"SN-UNIT-004")
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                    (role,))
        with pytest.raises(AutoStockDenied):
            register_lot_serial(db,d["tokens"][d["intake"]],
                org,loc,lot,key,"SN-UNIT-001")
