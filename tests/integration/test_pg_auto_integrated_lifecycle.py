"""PG17 integrated synthetic auto appointment -> work order -> stock lifecycle.

Checks cross-tenant denial and restoration of stock after failed mutation. This is
not a payment, production PII, or complete automotive acceptance scenario.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_appointment_service import create_appointment, AppointmentDenied
from roboport_api.auto_stock_service import AutoStockDenied
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_ledger import sku, balance, record
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def test_integrated_scoped_appointment_work_order_stock_and_rollback():
    d = _scenario()
    start = datetime.now(timezone.utc) + timedelta(days=6)
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        foreign_org, foreign_loc, foreign_bay = _org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"], d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""", (uuid4(),org,loc,d["intake"]))
        token = d["tokens"][d["intake"]]
        with pytest.raises(AppointmentDenied):
            create_appointment(db,token,foreign_org,foreign_loc,foreign_bay,d["link"],
                               start,start+timedelta(hours=1))
        appt, booking, order = create_appointment(
            db, token, org, loc, bay, d["link"], start, start+timedelta(hours=1),
        )
        assert db.execute("SELECT appointment_id,organization_id,location_id FROM auto_work_order WHERE id=%s",
                          (order,)).fetchone() == (appt,org,loc)
        item = sku(db,org,loc)
        other_item = sku(db,foreign_org,foreign_loc)
        grant(db,d["intake"],org,loc)
        record(db,item,"RECEIPT",6)
        def move(organization, location, article, action, qty):
            return mutate_work_order_stock(db,token,organization,location,order,
                                           article,uuid4(),action,qty)
        with pytest.raises(AutoStockDenied):
            move(foreign_org,foreign_loc,other_item,"RESERVE",1)
        with pytest.raises(AutoStockDenied):
            move(org,loc,other_item,"RESERVE",1)
        move(org,loc,item,"RESERVE",4)
        assert balance(db,item)==(6,4)
        move(org,loc,item,"ISSUE",2)
        assert balance(db,item)==(4,2)
        with pytest.raises(RuntimeError):
            with db.transaction():
                move(org,loc,item,"ISSUE",1)
                raise RuntimeError("synthetic rollback")
        assert balance(db,item)==(4,2)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_stock_event WHERE work_order_id=%s AND action='ISSUE'",
            (order,),
        ).fetchone()[0] == 1
        move(org,loc,item,"RELEASE",2)
        assert balance(db,item)==(4,0)
        assert db.execute("SELECT canceled_at FROM auto_bay_booking WHERE id=%s",
                          (booking,)).fetchone()[0] is None
