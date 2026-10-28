"""PG17 employee reservation: scoped shift, exclusion, cancellation and races."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def _setup(db, employee, start):
    org,loc,bay=_org_and_bay(db)
    db.execute("""INSERT INTO auto_staff_shift
      (id,organization_id,location_id,user_id,starts_at,ends_at)
      VALUES(%s,%s,%s,%s,%s,%s)""",
      (uuid4(),org,loc,employee,start,start+timedelta(hours=8)))
    return org,loc,bay


def _book(db, org, loc, bay, start, end):
    booking=uuid4()
    db.execute("""INSERT INTO auto_bay_booking
       (id,organization_id,location_id,bay_id,starts_at,ends_at)
       VALUES(%s,%s,%s,%s,%s,%s)""",(booking,org,loc,bay,start,end))
    return booking


def _reserve(db, booking, org, loc, employee, start, end):
    reservation=uuid4()
    db.execute("""INSERT INTO auto_employee_reservation
      (id,booking_id,organization_id,location_id,employee_id,starts_at,ends_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
      (reservation,booking,org,loc,employee,start,end))
    return reservation


def test_employee_reservation_scope_overlap_and_release():
    employee=_scenario()["intake"]
    start=datetime.now(timezone.utc)+timedelta(days=8)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_setup(db,employee,start)
        other,other_loc,other_bay=_org_and_bay(db)
        b1=_book(db,org,loc,bay,start,start+timedelta(hours=1))
        r1=_reserve(db,b1,org,loc,employee,start,start+timedelta(hours=1))
        b2=_book(db,other,other_loc,other_bay,start,start+timedelta(hours=1))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,b2,other,other_loc,employee,start,start+timedelta(hours=1))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,b1,other,other_loc,employee,start,start+timedelta(hours=1))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,b1,org,loc,employee,start,start+timedelta(minutes=59))
        adjacent=_book(db,org,loc,bay,start+timedelta(hours=1),start+timedelta(hours=2))
        _reserve(db,adjacent,org,loc,employee,
                 start+timedelta(hours=1),start+timedelta(hours=2))
        # A different bay can be booked, but the same employee cannot overlap.
        other_bay_id=uuid4()
        db.execute("""INSERT INTO auto_work_bay
          (id,organization_id,location_id,code)
          VALUES(%s,%s,%s,'BAY_2')""",(other_bay_id,org,loc))
        conflict=_book(db,org,loc,other_bay_id,start,start+timedelta(minutes=30))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with db.transaction():
                _reserve(db,conflict,org,loc,employee,start,start+timedelta(minutes=30))
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",(b1,))
        assert db.execute("SELECT canceled_at IS NOT NULL FROM auto_employee_reservation WHERE id=%s",
                          (r1,)).fetchone()==(True,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,b1,org,loc,employee,start,start+timedelta(hours=1))
        replacement=_reserve(db,conflict,org,loc,employee,start,start+timedelta(minutes=30))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_employee_reservation WHERE id=%s",(replacement,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_employee_reservation SET canceled_at=NULL WHERE id=%s",(r1,))


def test_employee_reservation_requires_full_active_shift():
    employee=_scenario()["intake"]
    start=datetime.now(timezone.utc)+timedelta(days=9)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_setup(db,employee,start)
        outside=_book(db,org,loc,bay,start+timedelta(hours=7),start+timedelta(hours=9))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,outside,org,loc,employee,start+timedelta(hours=7),
                         start+timedelta(hours=9))
        db.execute("""UPDATE auto_staff_shift SET canceled_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s""",
          (org,loc,employee))
        inside=_book(db,org,loc,bay,start,start+timedelta(hours=1))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _reserve(db,inside,org,loc,employee,start,start+timedelta(hours=1))


def test_employee_reservation_concurrent_overlap_is_serialized():
    employee=_scenario()["intake"]
    start=datetime.now(timezone.utc)+timedelta(days=10)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_setup(db,employee,start)
        another_bay=uuid4()
        db.execute("""INSERT INTO auto_work_bay
          (id,organization_id,location_id,code)
          VALUES(%s,%s,%s,'BAY_2')""",(another_bay,org,loc))
        first=_book(db,org,loc,bay,start,start+timedelta(hours=1))
        second=_book(db,org,loc,another_bay,
                     start+timedelta(minutes=15),start+timedelta(hours=1))
    with psycopg.connect(DSN) as one, psycopg.connect(DSN) as two:
        with one.transaction():
            _reserve(one,first,org,loc,employee,start,start+timedelta(hours=1))
            two.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with two.transaction():
                    _reserve(two,second,org,loc,employee,
                             start+timedelta(minutes=15),start+timedelta(hours=1))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with two.transaction():
                _reserve(two,second,org,loc,employee,
                         start+timedelta(minutes=15),start+timedelta(hours=1))


def test_cancellation_started_before_reservation_creation_does_not_roll_back():
    """Transaction-time now() must not invalidate later reservation created_at."""
    employee=_scenario()["intake"]
    start=datetime.now(timezone.utc)+timedelta(days=11)
    with psycopg.connect(DSN) as setup:
        org,loc,bay=_setup(setup,employee,start)
        booking=_book(setup,org,loc,bay,start,start+timedelta(hours=1))
    with psycopg.connect(DSN) as cancel, psycopg.connect(DSN) as reserve:
        with cancel.transaction():
            # Pin transaction timestamp before the reservation INSERT commits.
            old_timestamp=cancel.execute("SELECT now()").fetchone()[0]
            ident=_reserve(reserve,booking,org,loc,employee,start,
                           start+timedelta(hours=1))
            created=reserve.execute(
                "SELECT created_at FROM auto_employee_reservation WHERE id=%s",
                (ident,)).fetchone()[0]
            assert created>=old_timestamp
            reserve.commit()  # Release booking row lock before cancellation updates it.
            cancel.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
                           (booking,))
        with psycopg.connect(DSN) as verify:
            canceled=verify.execute(
                "SELECT created_at,canceled_at FROM auto_employee_reservation WHERE id=%s",
                (ident,)).fetchone()
            assert canceled[1] is not None
            assert canceled[1]>=canceled[0]
            assert verify.execute(
                "SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s",
                (booking,)).fetchone()==(True,)
