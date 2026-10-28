"""PG17 reschedule retains appointment and WorkOrder identity and history."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_appointment_service import (
    create_appointment,reschedule_appointment,AppointmentConflict,current_appointment_booking,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

def test_reschedule_keeps_workorder_releases_old_slot_and_audits():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
            (id,organization_id,location_id,user_id,role_code)
            VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
    start=datetime.now(timezone.utc)+timedelta(days=3)
    with psycopg.connect(DSN) as db:
        a,b,w=create_appointment(db,d["tokens"][d["intake"]],org,loc,bay,d["link"],
                                 start,start+timedelta(hours=1))
        with pytest.raises(AppointmentConflict):
            reschedule_appointment(db,d["tokens"][d["intake"]],org,a,bay,
                                   start+timedelta(minutes=10),start+timedelta(hours=2))
        assert current_appointment_booking(db,a)==b
        fresh=reschedule_appointment(db,d["tokens"][d["intake"]],org,a,bay,
                                     start+timedelta(hours=2),start+timedelta(hours=3))
        assert fresh!=b
        assert current_appointment_booking(db,a)==fresh
        assert db.execute("SELECT appointment_id FROM auto_work_order WHERE id=%s",(w,)).fetchone()==(a,)
        assert db.execute("SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",(b,)).fetchone()==(True,)
        assert db.execute("SELECT actor_id FROM auto_appointment_reschedule WHERE old_booking_id=%s",
                          (b,)).fetchone()==(d["intake"],)
        assert [x[0] for x in db.execute("SELECT action FROM auto_appointment_event WHERE appointment_id=%s ORDER BY id",
                                        (a,))]==["CREATED"]
        assert [x[0] for x in db.execute("SELECT action FROM auto_work_order_event WHERE work_order_id=%s ORDER BY id",
                                        (w,))]==["CREATED"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_appointment_reschedule WHERE old_booking_id=%s",(b,))
