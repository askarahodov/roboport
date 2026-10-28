"""PG17 immutable WorkOrder event history on booking cancellation."""
from uuid import uuid4
import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_baseline import DSN


def test_work_order_receipts_created_canceled_exactly_once():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2035-01-01 08:00+00','2035-01-01 09:00+00')
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        appointment, work_order = uuid4(), uuid4()
        db.execute("""INSERT INTO auto_appointment
          (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (appointment,booking,org,loc,d["vehicle"],d["link"]))
        db.execute("""INSERT INTO auto_work_order(id,appointment_id,organization_id,location_id)
          VALUES(%s,%s,%s,%s)""", (work_order,appointment,org,loc))
        def actions():
            return [x[0] for x in db.execute(
                "SELECT action FROM auto_work_order_event WHERE work_order_id=%s ORDER BY id",
                (work_order,)).fetchall()]
        assert actions()==["CREATED"]
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        db.execute("UPDATE auto_bay_booking SET canceled_at=canceled_at WHERE id=%s",(booking,))
        assert actions()==["CREATED","APPOINTMENT_CANCELED"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_event WHERE work_order_id=%s",(work_order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order_event SET action='CREATED' WHERE work_order_id=%s",(work_order,))


def test_cancel_unattached_booking_does_not_create_workorder_receipt():
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        booking = _book(db,org,loc,bay,'2035-02-01 08:00+00','2035-02-01 09:00+00')
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(booking,))
        assert db.execute("""SELECT count(*) FROM auto_work_order_event e
            JOIN auto_work_order w ON w.id=e.work_order_id
            JOIN auto_appointment a ON a.id=w.appointment_id WHERE a.booking_id=%s""",
            (booking,)).fetchone()[0]==0
