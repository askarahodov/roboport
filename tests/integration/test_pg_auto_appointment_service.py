"""PG17 atomic creation with tenant authority and resource scope."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
import pytest
from roboport_api.auto_appointment_service import create_appointment, AppointmentDenied, AppointmentConflict
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
