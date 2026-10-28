"""PG17 lifecycle capture: appointment creation and booking cancellation."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_baseline import DSN


def test_appointment_event_history_is_atomic_immutable_and_once():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db, org, loc, bay, '2033-04-01 08:00+00', '2033-04-01 10:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(), verified_by=%s WHERE id=%s",
                   (d["manager"], d["link"]))
        ident = uuid4()
        db.execute("""INSERT INTO auto_appointment
           (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
           VALUES (%s,%s,%s,%s,%s,%s)""",
           (ident,booking,org,loc,d["vehicle"],d["link"]))
        def history():
            return [row[0] for row in db.execute(
                "SELECT action FROM auto_appointment_event WHERE appointment_id=%s ORDER BY id",
                (ident,)).fetchall()]
        assert history() == ["CREATED"]
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s", (booking,))
        db.execute("UPDATE auto_bay_booking SET canceled_at=canceled_at WHERE id=%s", (booking,))
        assert history() == ["CREATED","BOOKING_CANCELED"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_appointment_event WHERE appointment_id=%s", (ident,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_appointment_event SET action='CREATED' WHERE appointment_id=%s", (ident,))


def test_canceled_booking_without_appointment_creates_no_phantom_event():
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db, org, loc, bay, '2033-05-01 08:00+00','2033-05-01 10:00+00')
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s", (booking,))
        assert db.execute("SELECT count(*) FROM auto_appointment_event").fetchone()[0] == 0
