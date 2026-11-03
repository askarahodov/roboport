"""PG17: lot ISSUE/RETURN and order stock ledger follow the current booking."""
from datetime import datetime, timezone
from uuid import uuid4

import psycopg

from roboport_api.auto_appointment_service import (
    reschedule_appointment, cancel_appointment,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup, issue
from test_pg_auto_stock_lot_work_order_return import ret
from test_pg_auto_stock_lot_allocation import allocated
from test_pg_auto_stock_ledger import balance
from test_pg_auto_work_order_stock import act
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def test_lot_issue_and_return_after_reschedule_stays_atomic_and_reconciles():
    d=_scenario()
    token=d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d)
        grant(db,d["intake"],org,loc,"INTAKE")
        db.execute(
            """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
               VALUES(%s,%s)""",
            (d["vehicle"],loc),
        )
        appointment,old=db.execute(
            """SELECT a.id,a.booking_id FROM auto_appointment a
               JOIN auto_work_order w ON w.appointment_id=a.id
               WHERE w.id=%s""",
            (order,),
        ).fetchone()
        later=datetime(2035,2,1,11,0,tzinfo=timezone.utc)
        new=reschedule_appointment(
            db,token,org,appointment,
            db.execute("SELECT bay_id FROM auto_bay_booking WHERE id=%s",(old,)).fetchone()[0],
            later,later.replace(hour=12),request_key=uuid4(),
        )
        assert new!=old
        assert db.execute(
            "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",(old,)
        ).fetchone()==(True,)
        issued=issue(db,d,org,loc,order,lot,2)
        assert issued
        assert allocated(db,lot)==3
        assert balance(db,item)==(3,3)
        returned=ret(db,d,org,loc,order,lot,2)
        assert returned
        assert allocated(db,lot)==5
        assert balance(db,item)==(5,3)
        act(db,d,org,loc,order,item,"RELEASE",3)
        assert balance(db,item)==(5,0)
        assert cancel_appointment(db,token,org,appointment)=="CANCELED"
        assert db.execute(
            "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",(new,)
        ).fetchone()==(True,)
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==3
