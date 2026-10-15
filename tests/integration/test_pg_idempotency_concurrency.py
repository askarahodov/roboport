"""Actual PostgreSQL concurrency and idempotency constraints on synthetic values."""
import threading
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_idempotency_scope_fingerprint_and_terminal_response():
    actor, key = uuid4(), "a" * 64
    with psycopg.connect(DSN) as conn:
        request_id = uuid4()
        expiry = datetime.now(timezone.utc) + timedelta(days=1)
        args = (request_id, actor, "approve-estimate", key, "b" * 64, expiry)
        conn.execute("""
          INSERT INTO idempotency_request(id,actor_id,operation,key_hash,request_fingerprint,expires_at)
          VALUES (%s,%s,%s,%s,%s,%s)
        """, args)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with conn.transaction():
                conn.execute("""
                  INSERT INTO idempotency_request(id,actor_id,operation,key_hash,request_fingerprint,expires_at)
                  VALUES (%s,%s,%s,%s,%s,%s)
                """, (uuid4(), actor, "approve-estimate", key, "c" * 64, expiry))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE idempotency_request SET request_fingerprint=%s WHERE id=%s",("c" * 64, request_id))
        conn.execute("UPDATE idempotency_request SET state='COMPLETED',response_code=200 WHERE id=%s",(request_id,))
        with pytest.raises(psycopg.errors.RaiseException):
            with conn.transaction():
                conn.execute("UPDATE idempotency_request SET state='FAILED' WHERE id=%s",(request_id,))
        assert conn.execute("SELECT state,response_code FROM idempotency_request WHERE id=%s",(request_id,)).fetchone() == ("COMPLETED",200)


def test_atomic_stock_reservation_two_contenders_only_one_succeeds():
    lot = uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO stock_lot(id,part_number,available_qty,reserved_qty) VALUES (%s,'atomic-lot',1,0)",(lot,))
    barrier = threading.Barrier(2)
    results = []
    errors = []

    def reserve():
        try:
            with psycopg.connect(DSN) as conn:
                barrier.wait(timeout=15)
                row = conn.execute("""
                  UPDATE stock_lot SET reserved_qty = reserved_qty + 1
                  WHERE id=%s AND reserved_qty < available_qty
                  RETURNING reserved_qty
                """,(lot,)).fetchone()
                results.append(row is not None)
        except Exception as exc:
            errors.append(repr(exc))

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=20)
    assert not errors, errors
    assert not any(t.is_alive() for t in threads)
    assert sorted(results) == [False, True], results
    with psycopg.connect(DSN) as conn:
        assert conn.execute("SELECT reserved_qty,available_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone() == (1,1)
