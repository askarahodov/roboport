"""PG17 races between new booking and disabling the selected resource."""
from uuid import uuid4

import psycopg
import pytest
from test_pg_auto_bay_booking import _org_and_bay, _book
from test_pg_baseline import DSN


def test_booking_holds_bay_share_lock_until_commit_and_disable_denies_next():
    with psycopg.connect(DSN) as setup:
        org, loc, bay = _org_and_bay(setup)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            _book(first, org, loc, bay, '2032-06-01 08:00+00', '2032-06-01 09:00+00')
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    second.execute("UPDATE auto_work_bay SET disabled_at=now() WHERE id=%s", (bay,))
        second.execute("UPDATE auto_work_bay SET disabled_at=now() WHERE id=%s", (bay,))
        with pytest.raises(psycopg.errors.RaiseException):
            with second.transaction():
                _book(second, org, loc, bay, '2032-06-01 10:00+00', '2032-06-01 11:00+00')


def test_booking_locks_location_and_organization_while_verifying_scope():
    with psycopg.connect(DSN) as setup:
        org, loc, bay = _org_and_bay(setup)
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            _book(first, org, loc, bay, '2032-06-02 08:00+00', '2032-06-02 09:00+00')
            second.execute("SET lock_timeout='200ms'")
            for table, ident in (("auto_service_location",loc), ("auto_organization",org)):
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with second.transaction():
                        second.execute(f"UPDATE {table} SET disabled_at=now() WHERE id=%s", (ident,))
        second.execute("UPDATE auto_service_location SET disabled_at=now() WHERE id=%s", (loc,))
        with pytest.raises(psycopg.errors.RaiseException):
            with second.transaction():
                _book(second, org, loc, bay, '2032-06-02 10:00+00','2032-06-02 11:00+00')
