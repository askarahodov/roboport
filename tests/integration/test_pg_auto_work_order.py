"""Synthetic PG17 work order cannot cross tenant, duplicate or mutate."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_baseline import DSN


def test_work_order_requires_live_appointment_and_exact_organization():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other, other_loc, _ = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2034-01-01 08:00+00','2034-01-01 09:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        appointment = uuid4()
        db.execute("""INSERT INTO auto_appointment
          (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (appointment,booking,org,loc,d["vehicle"],d["link"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
                  VALUES(%s,%s,%s,%s)""",(uuid4(),appointment,other,other_loc))
        order = uuid4()
        db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
          VALUES(%s,%s,%s,%s)""",(order,appointment,org,loc))
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
                  VALUES(%s,%s,%s,%s)""",(uuid4(),appointment,org,loc))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order SET organization_id=%s WHERE id=%s",(other,order))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order WHERE id=%s",(order,))


def test_canceled_appointment_cannot_create_new_work_order():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2034-02-01 08:00+00','2034-02-01 09:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        appointment = uuid4()
        db.execute("""INSERT INTO auto_appointment
          (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (appointment,booking,org,loc,d["vehicle"],d["link"]))
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
                  VALUES(%s,%s,%s,%s)""",(uuid4(),appointment,org,loc))
