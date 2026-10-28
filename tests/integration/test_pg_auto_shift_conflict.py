"""PG17 exclusion: concurrent employee shift overlaps fail atomically."""
from uuid import uuid4

import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_active_shifts_cannot_overlap_but_adjacency_and_cancellation_work():
    d = _scenario()
    org, loc = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                   (org, "ORG_" + org.hex[:10].upper()))
        db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                   (loc, "LOC_" + loc.hex[:10].upper(), org))
        first = uuid4()
        db.execute("""INSERT INTO auto_staff_shift
            (id,organization_id,location_id,user_id,starts_at,ends_at)
            VALUES (%s,%s,%s,%s,'2030-01-01 08:00+00','2030-01-01 16:00+00')""",
                   (first, org, loc, d["intake"]))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with db.transaction():
                db.execute("""INSERT INTO auto_staff_shift
                    (id,organization_id,location_id,user_id,starts_at,ends_at)
                    VALUES (%s,%s,%s,%s,'2030-01-01 10:00+00','2030-01-01 18:00+00')""",
                           (uuid4(), org, loc, d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
            (id,organization_id,location_id,user_id,starts_at,ends_at)
            VALUES (%s,%s,%s,%s,'2030-01-01 16:00+00','2030-01-01 20:00+00')""",
                   (uuid4(), org, loc, d["intake"]))
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s", (first,))
        db.execute("""INSERT INTO auto_staff_shift
            (id,organization_id,location_id,user_id,starts_at,ends_at)
            VALUES (%s,%s,%s,%s,'2030-01-01 08:00+00','2030-01-01 16:00+00')""",
                   (uuid4(), org, loc, d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
            (id,organization_id,location_id,user_id,starts_at,ends_at)
            VALUES (%s,%s,%s,%s,'2030-01-01 10:00+00','2030-01-01 18:00+00')""",
                   (uuid4(), org, loc, d["manager"]))


def test_concurrent_shift_insert_waits_on_exclusion_and_cannot_double_book():
    d = _scenario()
    org, loc = uuid4(), uuid4()
    with psycopg.connect(DSN) as setup:
        setup.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                      (org, "ORG_" + org.hex[:10].upper()))
        setup.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                      (loc, "LOC_" + loc.hex[:10].upper(), org))
    with psycopg.connect(DSN) as first, psycopg.connect(DSN) as second:
        with first.transaction():
            first.execute("""INSERT INTO auto_staff_shift
                (id,organization_id,location_id,user_id,starts_at,ends_at)
                VALUES (%s,%s,%s,%s,'2031-02-01 09:00+00','2031-02-01 17:00+00')""",
                          (uuid4(), org, loc, d["intake"]))
            second.execute("SET lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    second.execute("""INSERT INTO auto_staff_shift
                        (id,organization_id,location_id,user_id,starts_at,ends_at)
                        VALUES (%s,%s,%s,%s,'2031-02-01 10:00+00','2031-02-01 14:00+00')""",
                                   (uuid4(), org, loc, d["intake"]))
        with pytest.raises(psycopg.errors.ExclusionViolation):
            with second.transaction():
                second.execute("""INSERT INTO auto_staff_shift
                    (id,organization_id,location_id,user_id,starts_at,ends_at)
                    VALUES (%s,%s,%s,%s,'2031-02-01 10:00+00','2031-02-01 14:00+00')""",
                               (uuid4(), org, loc, d["intake"]))
