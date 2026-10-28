"""PG17 atomic creation with tenant authority and resource scope."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_appointment_service import create_appointment, reschedule_appointment, cancel_appointment, AppointmentDenied, AppointmentConflict
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

def test_atomic_creation_requires_intake_scope_and_rejects_conflicts():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay = _org_and_bay(db)
        other,other_loc,other_bay = _org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
    starts=datetime.now(timezone.utc)+timedelta(days=2)
    ends=starts+timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        with pytest.raises(AppointmentDenied):
            create_appointment(db,d["tokens"][d["manager"]],org,loc,bay,d["link"],starts,ends)
        with pytest.raises(AppointmentDenied):
            create_appointment(db,d["tokens"][d["intake"]],other,other_loc,other_bay,d["link"],starts,ends)
        a,b,w=create_appointment(db,d["tokens"][d["intake"]],org,loc,bay,d["link"],starts,ends)
        assert db.execute("SELECT appointment_id FROM auto_work_order WHERE id=%s",(w,)).fetchone()==(a,)
        with pytest.raises(AppointmentConflict):
            create_appointment(db,d["tokens"][d["intake"]],org,loc,bay,d["link"],starts,ends)
        assert db.execute("SELECT count(*) FROM auto_bay_booking WHERE bay_id=%s",(bay,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM auto_appointment WHERE booking_id=%s",(b,)).fetchone()[0]==1


def test_employee_assignment_create_reschedule_cancel_and_atomic_rollback():
    d=_scenario()
    starts=datetime.now(timezone.utc)+timedelta(days=7)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["intake"],starts,starts+timedelta(hours=8)))
    with psycopg.connect(DSN) as db:
        token=d["tokens"][d["intake"]]
        with pytest.raises(AppointmentConflict):
            create_appointment(db,token,org,loc,bay,d["link"],
                               starts-timedelta(minutes=1),starts+timedelta(hours=1),
                               d["intake"])
        assert db.execute("SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)).fetchone()[0]==0
        a,b,w=create_appointment(db,token,org,loc,bay,d["link"],starts,
                                  starts+timedelta(hours=1),d["intake"])
        assert db.execute("SELECT employee_id FROM auto_employee_reservation WHERE booking_id=%s",
                          (b,)).fetchone()==(d["intake"],)
        fresh=reschedule_appointment(db,token,org,a,bay,
                         starts+timedelta(hours=2),starts+timedelta(hours=3))
        assert db.execute("SELECT canceled_at IS NOT NULL FROM auto_employee_reservation WHERE booking_id=%s",
                          (b,)).fetchone()==(True,)
        assert db.execute("SELECT employee_id FROM auto_employee_reservation WHERE booking_id=%s",
                          (fresh,)).fetchone()==(d["intake"],)
        with pytest.raises(AppointmentConflict):
            reschedule_appointment(db,token,org,a,bay,
                         starts+timedelta(hours=7),starts+timedelta(hours=9))
        assert db.execute("SELECT canceled_at FROM auto_bay_booking WHERE id=%s",
                          (fresh,)).fetchone()[0] is None
        assert cancel_appointment(db,token,org,a)=="CANCELED"
        assert db.execute("SELECT canceled_at IS NOT NULL FROM auto_employee_reservation WHERE booking_id=%s",
                          (fresh,)).fetchone()==(True,)
