"""Appointment identity requires current verified link and exact booking scope."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_baseline import DSN


def test_appointment_requires_verified_vehicle_link_and_exact_booking_scope():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other_org, other_loc, _ = _org_and_bay(db)
        booking = _book(db, org, loc, bay, '2032-01-01 08:00+00','2032-01-01 10:00+00')
        ident = uuid4()
        def add(scope=org, place=loc, link=None):
            db.execute("""INSERT INTO auto_appointment
              (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
              VALUES(%s,%s,%s,%s,%s,%s)""",
              (ident, booking, scope, place, d["vehicle"], link or d["link"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add()
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                add(other_org,other_loc)
        add()
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("""INSERT INTO auto_appointment
                  (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                  VALUES(%s,%s,%s,%s,%s,%s)""",
                  (uuid4(), booking, org, loc, d["vehicle"], d["link"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_appointment WHERE id=%s", (ident,))


def test_canceled_booking_and_revoked_link_do_not_create_appointment():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2032-02-01 08:00+00','2032-02-01 10:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s,revoked_at=now() WHERE id=%s",
                   (d["manager"],d["link"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_appointment
                (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (uuid4(),booking,org,loc,d["vehicle"],d["link"]))
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_appointment
                (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (uuid4(),booking,org,loc,d["vehicle"],d["other_link"]))
