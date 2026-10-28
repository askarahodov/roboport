"""PG17 booking constraints: tenant isolation, overlap, adjacency and cancellation."""
from uuid import uuid4

import psycopg
import pytest
from test_pg_baseline import DSN


def _org_and_bay(db):
    org, loc, bay = uuid4(), uuid4(), uuid4()
    db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
               (org, "ORG_" + org.hex[:10].upper()))
    db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
               (loc, "LOC_" + loc.hex[:10].upper(), org))
    db.execute("INSERT INTO auto_work_bay(id,organization_id,location_id,code) VALUES(%s,%s,%s,'BAY_1')",
               (bay, org, loc))
    return org, loc, bay


def _book(db, org, loc, bay, start, end):
    ident = uuid4()
    db.execute("""INSERT INTO auto_bay_booking
      (id,organization_id,location_id,bay_id,starts_at,ends_at)
      VALUES(%s,%s,%s,%s,%s,%s)""", (ident, org, loc, bay, start, end))
    return ident


def test_booking_conflict_adjacency_and_cancellation():
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        first = _book(db, org, loc, bay, '2030-01-01 08:00+00','2030-01-01 12:00+00')
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with db.transaction():
                _book(db, org, loc, bay, '2030-01-01 10:00+00','2030-01-01 13:00+00')
        _book(db, org, loc, bay, '2030-01-01 12:00+00','2030-01-01 14:00+00')
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s", (first,))
        _book(db, org, loc, bay, '2030-01-01 08:00+00','2030-01-01 12:00+00')
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_bay_booking SET canceled_at=NULL WHERE id=%s", (first,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_bay_booking WHERE id=%s", (first,))


def test_booking_rejects_foreign_location_and_disabled_bay():
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other, other_loc, other_bay = _org_and_bay(db)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _book(db, other, other_loc, bay, '2030-04-01 08:00+00','2030-04-01 09:00+00')
        db.execute("UPDATE auto_work_bay SET disabled_at=now() WHERE id=%s", (other_bay,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                _book(db, other, other_loc, other_bay, '2030-04-01 08:00+00','2030-04-01 09:00+00')


def test_booking_concurrency_waits_then_fails_on_exclusion():
    with psycopg.connect(DSN) as setup:
        org, loc, bay = _org_and_bay(setup)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            _book(first, org, loc, bay, '2030-05-01 08:00+00','2030-05-01 12:00+00')
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    _book(second, org, loc, bay, '2030-05-01 09:00+00','2030-05-01 11:00+00')
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with second.transaction():
                _book(second, org, loc, bay, '2030-05-01 09:00+00','2030-05-01 11:00+00')
