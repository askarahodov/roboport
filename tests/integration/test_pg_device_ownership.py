"""Real PostgreSQL active device owner and immutable order ownership checks."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
import pytest
from test_pg_baseline import DSN


def test_active_device_owner_unique_and_history_immutable():
    customer_a,customer_b,device,owner_a,owner_b,ticket=(uuid4() for _ in range(6))
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES (%s,'owner-a'),(%s,'owner-b')",
                   (customer_a,customer_b))
        db.execute("INSERT INTO robot_device(id) VALUES (%s)",(device,))
        db.execute("INSERT INTO service_ticket(id,customer_id,device_id) VALUES (%s,%s,%s)",
                   (ticket,customer_a,device))
        db.execute("""INSERT INTO device_ownership(id,device_id,customer_id)
                      VALUES (%s,%s,%s)""",(owner_a,device,customer_a))
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("""INSERT INTO device_ownership(id,device_id,customer_id)
                              VALUES (%s,%s,%s)""",(owner_b,device,customer_b))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE device_ownership SET customer_id=%s WHERE id=%s",
                           (customer_b,owner_a))
        db.execute("UPDATE device_ownership SET ended_at=clock_timestamp() WHERE id=%s",(owner_a,))
        db.execute("""INSERT INTO device_ownership(id,device_id,customer_id)
                      VALUES (%s,%s,%s)""",(owner_b,device,customer_b))
        assert db.execute("""SELECT customer_id FROM device_ownership
                             WHERE device_id=%s AND ended_at IS NULL""",
                          (device,)).fetchone()[0]==customer_b
        assert db.execute("SELECT customer_id FROM service_ticket WHERE id=%s",
                          (ticket,)).fetchone()[0]==customer_a
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE device_ownership SET ended_at=NULL WHERE id=%s",
                           (owner_a,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM device_ownership WHERE id=%s",(owner_a,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE service_ticket SET customer_id=%s WHERE id=%s",
                           (customer_b,ticket))


def test_concurrent_two_owners_one_device():
    customer_a,customer_b,device=(uuid4() for _ in range(3))
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES (%s,'race-a'),(%s,'race-b')",
                   (customer_a,customer_b))
        db.execute("INSERT INTO robot_device(id) VALUES (%s)",(device,))
    barrier=Barrier(2)
    def claim(customer):
        try:
            with psycopg.connect(DSN) as db:
                barrier.wait(timeout=20)
                db.execute("""INSERT INTO device_ownership(id,device_id,customer_id)
                              VALUES (%s,%s,%s)""",(uuid4(),device,customer))
            return "success"
        except psycopg.errors.UniqueViolation:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(claim,c) for c in (customer_a,customer_b)]
        results=[f.result(timeout=30) for f in futures]
    assert sorted(results)==["conflict","success"],results
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM device_ownership
                             WHERE device_id=%s AND ended_at IS NULL""",
                          (device,)).fetchone()[0]==1


def test_ownership_fk_and_interval_constraints():
    customer,device=(uuid4() for _ in range(2))
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES (%s,'fk-owner')",(customer,))
        db.execute("INSERT INTO robot_device(id) VALUES (%s)",(device,))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                db.execute("""INSERT INTO device_ownership(id,device_id,customer_id)
                              VALUES (%s,%s,%s)""",(uuid4(),device,uuid4()))
        now=datetime.now(timezone.utc)
        with pytest.raises(psycopg.errors.CheckViolation):
            with db.transaction():
                db.execute("""INSERT INTO device_ownership
                       (id,device_id,customer_id,started_at,ended_at)
                       VALUES (%s,%s,%s,%s,%s)""",
                       (uuid4(),device,customer,now,now-timedelta(days=1)))
